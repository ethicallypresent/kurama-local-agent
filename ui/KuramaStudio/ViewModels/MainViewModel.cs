using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using KuramaStudio.Models;
using KuramaStudio.Services;
using Microsoft.Win32;
using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;

namespace KuramaStudio.ViewModels;

public partial class MainViewModel : ObservableObject
{
    private const double StreamTokensPerSecond = 16.0;

    private readonly ConfigService _configService;
    private readonly ChatHistoryService _chatHistory;
    private readonly BrainService _brain;
    private readonly OllamaService _ollama = new();
    private readonly AgentRunner _runner = new();
    private readonly ChatStreamService _chatStream = new();
    private CancellationTokenSource? _runCts;
    private ChatBubble? _liveBubble;
    private readonly StringBuilder _streamBuf = new();
    private readonly ConcurrentQueue<string> _displayQueue = new();
    private DispatcherTimer? _paceTimer;
    private bool _seenThinkOpen;
    private bool _thinkClosed;
    private bool _streamFinished;

    public MainViewModel()
    {
        try
        {
            App.Log("MainViewModel ctor");
            _configService = new ConfigService();
            _chatHistory = new ChatHistoryService(_configService.AgentRoot);
            _brain = new BrainService(_configService.AgentRoot);
            App.Log("agent root=" + _configService.AgentRoot);
            if (!_brain.LooksValid)
                throw new FileNotFoundException("Brain files missing: system_prompt.md / constitution.md under brain/");
            var brainPreview = _brain.LoadBrain();
            App.Log($"brain loaded chars={brainPreview.Length} (~{brainPreview.Length / 4} tok)");
            Config = _configService.Load();

            // Force llama.cpp — never leave Ollama URLs/tags from older configs.
            BaseUrl = "http://127.0.0.1:8080/v1";
            Config.BaseUrl = BaseUrl;
            if (string.IsNullOrWhiteSpace(Config.Model) || Config.Model.Contains(':') || Config.Model.Contains("ollama", StringComparison.OrdinalIgnoreCase))
                Config.Model = "pocket";
            if (string.IsNullOrWhiteSpace(Config.FallbackModel) || Config.FallbackModel.Contains(':'))
                Config.FallbackModel = "pocket";
            Config.EmbeddingUrl = "";
            Config.EmbeddingModel = "";
            SyncFromConfig();
            BaseUrl = "http://127.0.0.1:8080/v1"; // SyncFromConfig may re-read; pin again

            ScanGgufs();
            _chatHistory.LoadInto(Bubbles);
            if (Bubbles.Count == 0)
                Bubbles.Add(new ChatBubble("Kurama", "Hey — I'm Kurama. Give me a goal and I'll loop perceive → think → act.", true));

            _runner.LineReceived += line =>
                Application.Current.Dispatcher.Invoke(() => OnRunnerLine(line));
            _runner.Exited += code =>
                Application.Current.Dispatcher.Invoke(() => OnRunExited(code));

            App.LlamaServer.Log += line =>
                Application.Current.Dispatcher.Invoke(() => AppendLog("[llama] " + line));

            _ = BootstrapStackAsync();
        }
        catch (Exception ex)
        {
            App.Log("MainViewModel ctor failed: " + ex);
            StatusText = "Startup failed";
            ConnectionDetail = ex.Message;
            throw;
        }
    }

    private async Task BootstrapStackAsync()
    {
        StatusText = "Starting llama.cpp…";
        ConnectionDetail = $"Launching llama-server with {Path.GetFileName(EffectiveModelPath())}";
        try
        {
            // Persist endpoint so python main.py matches the UI
            PushToConfig();
            Config.BaseUrl = BaseUrl;
            Config.Model = string.IsNullOrWhiteSpace(SelectedChatModel) ? "pocket" : SelectedChatModel;
            Config.FallbackModel = string.IsNullOrWhiteSpace(SelectedFallbackModel) ? Config.Model : SelectedFallbackModel;
            Config.EmbeddingUrl = "";
            Config.EmbeddingModel = "";
            try
            {
                _configService.Save(Config);
                App.Log("config saved");
            }
            catch (Exception saveEx)
            {
                App.Log("config save failed (continuing): " + saveEx.Message);
            }

            App.Log("starting llama-server…");
            var ok = await App.LlamaServer.EnsureRunningAsync(_configService.AgentRoot, BuildLlamaOptions());
            App.Log(ok ? "llama-server ready" : "llama-server failed: " + App.LlamaServer.LastError);
            OllamaConnected = ok;
            StatusText = ok ? "llama.cpp online" : "llama.cpp failed";
            ConnectionDetail = ok
                ? $"Ready · {Path.GetFileName(App.LlamaServer.ModelPath)} · brain {_brain.LoadBrain().Length / 4}tok"
                : (App.LlamaServer.LastError ?? "start failed");
            if (ok) await RefreshConnectionAsync();
            else FlashToast(App.LlamaServer.LastError ?? "llama-server failed to start");
        }
        catch (Exception ex)
        {
            App.Log("BootstrapStack failed: " + ex);
            StatusText = "Startup error";
            ConnectionDetail = ex.Message;
            FlashToast(ex.Message);
        }
    }

    public AgentConfigModel Config { get; private set; }

    public ObservableCollection<string> ChatModels { get; } = new();
    public ObservableCollection<string> EmbedModels { get; } = new();
    public ObservableCollection<string> LoadedModels { get; } = new();
    public ObservableCollection<GgufFile> AvailableGgufs { get; } = new();
    public ObservableCollection<string> LogLines { get; } = new();
    public ObservableCollection<ChatBubble> Bubbles { get; } = new();

    [ObservableProperty] private string _selectedPage = "Chat";
    [ObservableProperty] private string _goalText = "say hello and list your tools";
    [ObservableProperty] private string _statusText = "Starting…";
    [ObservableProperty] private bool _ollamaConnected;
    [ObservableProperty] private bool _isRunning;
    [ObservableProperty] private bool _dryRun;
    [ObservableProperty] private bool _agentMode; // false = streamed chat Run; true = tool agent loop
    [ObservableProperty] private int _runMaxSteps = 12;
    [ObservableProperty] private string _connectionDetail = "";
    [ObservableProperty] private string _selectedChatModel = "";
    [ObservableProperty] private string _selectedFallbackModel = "";
    [ObservableProperty] private string _selectedEmbedModel = "";
    [ObservableProperty] private string _selectedModelPath = "models/pocket.gguf";
    [ObservableProperty] private GgufFile? _selectedGguf;
    [ObservableProperty] private bool _isLoadingModel;
    [ObservableProperty] private string _selectedPreset = "cpu_optimal";
    [ObservableProperty] private string _toast = "";
    [ObservableProperty] private bool _showToast;

    // Appearance
    [ObservableProperty] private double _uiScale = 1.0;
    [ObservableProperty] private bool _compactMode;
    [ObservableProperty] private string _accentHex = "#F59E0B";
    [ObservableProperty] private bool _personaMode = true;

    // Bound load knobs (synced both ways)
    [ObservableProperty] private int _numCtx = 32768;
    [ObservableProperty] private int _numBatch = 512;
    [ObservableProperty] private int _numGpu;
    [ObservableProperty] private int _numThread;
    [ObservableProperty] private int _maxTokens = 900;
    [ObservableProperty] private int _timeoutSec = 300;
    [ObservableProperty] private double _temperature = 0.2;
    [ObservableProperty] private double _topP = 0.9;
    [ObservableProperty] private int _topK = 40;
    [ObservableProperty] private double _repeatPenalty = 1.1;
    [ObservableProperty] private string _keepAlive = "10m";
    [ObservableProperty] private string _baseUrl = "http://127.0.0.1:8080/v1";
    [ObservableProperty] private bool _warmup = true;
    [ObservableProperty] private bool _dryRunOnNoServer = true;
    [ObservableProperty] private bool _skillTestRequired = true;
    [ObservableProperty] private bool _requireThinkBlock = true;
    [ObservableProperty] private bool _thinkMustCite = true;
    [ObservableProperty] private bool _codeRunnerNetwork;
    [ObservableProperty] private bool _logRawTranscripts;
    [ObservableProperty] private int _maxSteps = 50;
    [ObservableProperty] private int _toolTopK = 8;
    [ObservableProperty] private int _memoryTopK = 5;
    [ObservableProperty] private int _skillTopK = 8;

    public IReadOnlyList<(string Id, string Label, string Hint)> Presets => AgentConfigModel.LoadPresets;

    private void SyncFromConfig()
    {
        SelectedChatModel = Config.Model;
        SelectedFallbackModel = Config.FallbackModel;
        SelectedEmbedModel = Config.EmbeddingModel;
        SelectedModelPath = string.IsNullOrWhiteSpace(Config.ModelPath) ? "models/pocket.gguf" : Config.ModelPath;
        SelectedPreset = Config.LoadPreset;
        NumCtx = Config.NumCtx;
        NumBatch = Config.NumBatch;
        NumGpu = Config.NumGpu;
        NumThread = Config.NumThread;
        MaxTokens = Config.MaxTokens;
        TimeoutSec = Config.TimeoutSec;
        Temperature = Config.Temperature;
        TopP = Config.TopP;
        TopK = Config.TopK;
        RepeatPenalty = Config.RepeatPenalty;
        KeepAlive = Config.KeepAlive;
        BaseUrl = Config.BaseUrl;
        Warmup = Config.Warmup;
        DryRunOnNoServer = Config.DryRunOnNoServer;
        SkillTestRequired = Config.SkillTestRequired;
        RequireThinkBlock = Config.RequireThinkBlock;
        ThinkMustCite = Config.ThinkMustCiteSource;
        CodeRunnerNetwork = Config.CodeRunnerNetwork;
        LogRawTranscripts = Config.LogRawTranscripts;
        MaxSteps = Config.MaxSteps;
        ToolTopK = Config.ToolTopK;
        MemoryTopK = Config.MemoryTopK;
        SkillTopK = Config.SkillTopK;
    }

    private void PushToConfig()
    {
        Config.Model = SelectedChatModel;
        Config.ModelPath = string.IsNullOrWhiteSpace(SelectedModelPath) ? "models/pocket.gguf" : SelectedModelPath;
        Config.FallbackModel = SelectedFallbackModel;
        Config.EmbeddingModel = SelectedEmbedModel;
        Config.LoadPreset = SelectedPreset;
        Config.NumCtx = NumCtx;
        Config.NumBatch = NumBatch;
        Config.NumGpu = NumGpu;
        Config.NumThread = NumThread;
        Config.MaxTokens = MaxTokens;
        Config.TimeoutSec = TimeoutSec;
        Config.Temperature = Temperature;
        Config.TopP = TopP;
        Config.TopK = TopK;
        Config.RepeatPenalty = RepeatPenalty;
        Config.KeepAlive = KeepAlive;
        Config.BaseUrl = BaseUrl;
        Config.Warmup = Warmup;
        Config.DryRunOnNoServer = DryRunOnNoServer;
        Config.SkillTestRequired = SkillTestRequired;
        Config.RequireThinkBlock = RequireThinkBlock;
        Config.ThinkMustCiteSource = ThinkMustCite;
        Config.CodeRunnerNetwork = CodeRunnerNetwork;
        Config.LogRawTranscripts = LogRawTranscripts;
        Config.MaxSteps = MaxSteps;
        Config.ToolTopK = ToolTopK;
        Config.MemoryTopK = MemoryTopK;
        Config.SkillTopK = SkillTopK;
        Config.EmbeddingUrl = string.IsNullOrWhiteSpace(Config.EmbeddingUrl)
            ? BaseUrl
            : Config.EmbeddingUrl;
    }

    [RelayCommand]
    private void Navigate(string page) => SelectedPage = page;

    [RelayCommand]
    private async Task RefreshConnectionAsync()
    {
        // Always llama.cpp — never Ollama /api/tags.
        BaseUrl = "http://127.0.0.1:8080/v1";
        Config.BaseUrl = BaseUrl;
        StatusText = "Checking llama.cpp…";
        ConnectionDetail = "Probing http://127.0.0.1:8080/v1/models";

        var healthy = await App.LlamaServer.IsHealthyAsync() || await ProbeOpenAiAsync(BaseUrl);
        if (!healthy)
        {
            StatusText = "Starting llama.cpp…";
            ConnectionDetail = "llama-server not up — launching…";
            healthy = await App.LlamaServer.EnsureRunningAsync(_configService.AgentRoot, BuildLlamaOptions());
            if (!healthy)
            {
                OllamaConnected = false;
                StatusText = "llama.cpp offline";
                ConnectionDetail = App.LlamaServer.LastError ?? "Could not start llama-server";
                FlashToast(ConnectionDetail);
                return;
            }
        }

        OllamaConnected = true;
        StatusText = "llama.cpp online";
        string brainNote;
        try { brainNote = $"brain {_brain.LoadBrain().Length / 4}tok"; }
        catch (Exception ex) { brainNote = "brain ERROR: " + ex.Message; }
        ConnectionDetail = App.LlamaServer.ModelPath is string mp
            ? $"Ready · {Path.GetFileName(mp)} · {brainNote}"
            : $"Ready · pocket @ :8080 · {brainNote}";

        try
        {
            ChatModels.Clear();
            EmbedModels.Clear();
            LoadedModels.Clear();

            var ids = await App.LlamaServer.ListModelsAsync();
            if (ids.Count == 0)
                ids = await ListOpenAiModelsAsync(BaseUrl);

            foreach (var id in ids)
            {
                if (!ChatModels.Contains(id)) ChatModels.Add(id);
                LoadedModels.Add(id);
            }

            var alias = AliasForPath(EffectiveModelPath());
            if (!ChatModels.Contains(alias)) ChatModels.Insert(0, alias);

            // Strip leftover Ollama-style tags (pocket:latest) from older sessions.
            if (string.IsNullOrWhiteSpace(SelectedChatModel) || SelectedChatModel.Contains(':'))
                SelectedChatModel = alias;
            if (string.IsNullOrWhiteSpace(SelectedFallbackModel) || SelectedFallbackModel.Contains(':'))
                SelectedFallbackModel = SelectedChatModel;

            EmbedModels.Add("(hash fallback — no embed server)");
            SelectedEmbedModel = EmbedModels[0];
            FlashToast("Connected to llama.cpp");
        }
        catch (Exception ex)
        {
            ConnectionDetail = ex.Message;
            FlashToast(ex.Message);
        }
    }

    private static async Task<bool> ProbeOpenAiAsync(string baseUrl)
    {
        try
        {
            using var http = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(3) };
            using var resp = await http.GetAsync(baseUrl.TrimEnd('/') + "/models");
            return resp.IsSuccessStatusCode;
        }
        catch { return false; }
    }

    private static async Task<IReadOnlyList<string>> ListOpenAiModelsAsync(string baseUrl)
    {
        try
        {
            using var http = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            using var resp = await http.GetAsync(baseUrl.TrimEnd('/') + "/models");
            resp.EnsureSuccessStatusCode();
            await using var stream = await resp.Content.ReadAsStreamAsync();
            using var doc = await System.Text.Json.JsonDocument.ParseAsync(stream);
            var list = new List<string>();
            if (doc.RootElement.TryGetProperty("data", out var data))
            {
                foreach (var m in data.EnumerateArray())
                    if (m.TryGetProperty("id", out var id))
                        list.Add(id.GetString() ?? "");
            }
            return list;
        }
        catch { return Array.Empty<string>(); }
    }

    private string EffectiveModelPath()
    {
        var configured = string.IsNullOrWhiteSpace(SelectedModelPath) ? Config.ModelPath : SelectedModelPath;
        return LlamaServerService.ResolveModelPath(_configService.AgentRoot, configured);
    }

    private string AliasForPath(string path)
    {
        var name = Path.GetFileNameWithoutExtension(path);
        return string.IsNullOrWhiteSpace(name) ? "pocket" : name;
    }

    private string Relativize(string fullPath)
    {
        try
        {
            var root = Path.GetFullPath(_configService.AgentRoot);
            var full = Path.GetFullPath(fullPath);
            var rel = Path.GetRelativePath(root, full);
            if (!rel.StartsWith("..", StringComparison.Ordinal) && !Path.IsPathRooted(rel))
                return rel.Replace('\\', '/');
        }
        catch { /* keep absolute */ }
        return fullPath;
    }

    private LlamaServerOptions BuildLlamaOptions()
    {
        var path = EffectiveModelPath();
        return new LlamaServerOptions
        {
            AutoStart = true,
            Host = "127.0.0.1",
            Port = 8080,
            ModelPath = path,
            Alias = AliasForPath(path),
            NumCtx = NumCtx >= 512 ? NumCtx : 4096,
            NumThread = NumThread > 0 ? NumThread : Math.Max(2, Environment.ProcessorCount - 2),
            NumBatch = NumBatch > 0 ? NumBatch : 128,
            NumGpu = Math.Max(0, NumGpu),
            Jinja = true,
            StartupTimeoutSec = 180,
        };
    }

    public void ScanGgufs()
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var found = new List<GgufFile>();

        void AddIfExists(string path)
        {
            try
            {
                if (!File.Exists(path)) return;
                var full = Path.GetFullPath(path);
                if (!seen.Add(full)) return;
                found.Add(new GgufFile(full));
            }
            catch { /* skip unreadable */ }
        }

        var root = _configService.AgentRoot;
        var modelsDir = Path.Combine(root, "models");
        if (Directory.Exists(modelsDir))
        {
            foreach (var file in Directory.EnumerateFiles(modelsDir, "*.gguf", SearchOption.TopDirectoryOnly))
                AddIfExists(file);
        }
        AddIfExists(EffectiveModelPath());
        if (!string.IsNullOrWhiteSpace(SelectedModelPath))
            AddIfExists(LlamaServerService.ResolveModelPath(root, SelectedModelPath));

        found.Sort((a, b) => string.Compare(a.Name, b.Name, StringComparison.OrdinalIgnoreCase));
        AvailableGgufs.Clear();
        foreach (var item in found)
            AvailableGgufs.Add(item);

        var current = LlamaServerService.ResolveModelPath(root, SelectedModelPath);
        SelectedGguf = AvailableGgufs.FirstOrDefault(g =>
            string.Equals(g.FullPath, current, StringComparison.OrdinalIgnoreCase));
    }

    partial void OnSelectedGgufChanged(GgufFile? value)
    {
        if (value is null) return;
        SelectedModelPath = Relativize(value.FullPath);
    }

    [RelayCommand]
    private void BrowseGguf()
    {
        var start = Path.Combine(_configService.AgentRoot, "models");
        if (!Directory.Exists(start))
            start = _configService.AgentRoot;
        try
        {
            var existing = LlamaServerService.ResolveModelPath(_configService.AgentRoot, SelectedModelPath);
            var dir = Path.GetDirectoryName(existing);
            if (!string.IsNullOrWhiteSpace(dir) && Directory.Exists(dir))
                start = dir;
        }
        catch { /* keep models/ */ }

        var dlg = new OpenFileDialog
        {
            Title = "Select GGUF model",
            Filter = "GGUF weights (*.gguf)|*.gguf|All files (*.*)|*.*",
            DefaultExt = ".gguf",
            CheckFileExists = true,
            Multiselect = false,
            InitialDirectory = start,
        };
        if (dlg.ShowDialog() != true || string.IsNullOrWhiteSpace(dlg.FileName))
            return;

        SelectedModelPath = Relativize(dlg.FileName);
        var picked = new GgufFile(dlg.FileName);
        if (!AvailableGgufs.Contains(picked))
            AvailableGgufs.Insert(0, picked);
        SelectedGguf = picked;
        FlashToast($"Selected {picked.Name}");
    }

    [RelayCommand]
    private void ScanGgufFolder()
    {
        ScanGgufs();
        FlashToast(AvailableGgufs.Count == 0 ? "No .gguf files found in models/" : $"Found {AvailableGgufs.Count} GGUF file(s)");
    }

    [RelayCommand]
    private async Task LoadSelectedGgufAsync()
    {
        if (IsLoadingModel) return;
        var path = EffectiveModelPath();
        if (!File.Exists(path))
        {
            FlashToast($"GGUF not found: {path}");
            return;
        }

        IsLoadingModel = true;
        StatusText = "Stopping llama.cpp…";
        ConnectionDetail = "Freeing port so the new GGUF can load";
        try
        {
            SelectedChatModel = AliasForPath(path);
            SelectedFallbackModel = SelectedChatModel;
            PushToConfig();
            try { _configService.Save(Config); }
            catch (Exception saveEx) { App.Log("config save failed: " + saveEx.Message); }

            StatusText = "Loading GGUF…";
            ConnectionDetail = Path.GetFileName(path);
            var ok = await App.LlamaServer.EnsureRunningAsync(
                _configService.AgentRoot, BuildLlamaOptions(), CancellationToken.None, forceRestart: true);
            OllamaConnected = ok;
            StatusText = ok ? "llama.cpp online" : "llama.cpp failed";
            ConnectionDetail = ok
                ? $"Ready · {Path.GetFileName(App.LlamaServer.ModelPath)} · brain {_brain.LoadBrain().Length / 4}tok"
                : (App.LlamaServer.LastError ?? "failed to load GGUF");
            FlashToast(ok ? $"Loaded {Path.GetFileName(path)}" : ConnectionDetail);
            if (ok) await RefreshConnectionAsync();
        }
        catch (Exception ex)
        {
            StatusText = "Load failed";
            ConnectionDetail = ex.Message;
            FlashToast(ex.Message);
        }
        finally
        {
            IsLoadingModel = false;
        }
    }

    [RelayCommand]
    private async Task StopServerAsync()
    {
        StatusText = "Stopping llama.cpp…";
        ConnectionDetail = $"Freeing :{App.LlamaServer.Port}";
        try
        {
            var freed = await App.LlamaServer.StopAndWaitAsync();
            OllamaConnected = false;
            LoadedModels.Clear();
            StatusText = freed ? "llama.cpp stopped" : "Stop incomplete";
            ConnectionDetail = freed
                ? "Server is down. Browse a GGUF and Load to start again."
                : (App.LlamaServer.LastError ?? "Port still in use");
            FlashToast(StatusText);
        }
        catch (Exception ex)
        {
            StatusText = "Stop failed";
            ConnectionDetail = ex.Message;
            FlashToast(ex.Message);
        }
    }

    [RelayCommand]
    private void ApplyPreset(string? presetId)
    {
        if (string.IsNullOrWhiteSpace(presetId)) return;
        SelectedPreset = presetId;
        Config.ApplyPreset(presetId);
        SyncFromConfig();
        FlashToast($"Applied preset: {presetId}");
    }

    [RelayCommand]
    private void SaveConfig()
    {
        try
        {
            PushToConfig();
            _configService.Save(Config);
            FlashToast("Saved reasoning_config.json");
        }
        catch (Exception ex)
        {
            FlashToast($"Save failed: {ex.Message}");
        }
    }

    [RelayCommand]
    private async Task WarmupSelectedAsync()
    {
        PushToConfig();
        FlashToast($"Warming {SelectedChatModel}…");
        var options = new
        {
            num_ctx = NumCtx,
            num_batch = NumBatch,
            num_gpu = NumGpu,
            num_thread = NumThread > 0 ? NumThread : (int?)null,
        };
        using var cts = new CancellationTokenSource(TimeSpan.FromMinutes(5));
        var (ok, msg) = await _ollama.WarmupAsync(BaseUrl, SelectedChatModel, options, KeepAlive, cts.Token);
        FlashToast(msg);
        if (ok) await RefreshConnectionAsync();
    }

    [RelayCommand]
    private async Task RunAgentAsync()
    {
        if (IsRunning) return;
        if (string.IsNullOrWhiteSpace(GoalText))
        {
            FlashToast("Enter a goal first");
            return;
        }

        SaveConfig();
        LogLines.Clear();
        SelectedPage = "Chat";
        Bubbles.Add(new ChatBubble("You", GoalText, false));
        _liveBubble = new ChatBubble("Kurama", "", true)
        {
            IsThinking = true,
            ThinkingLabel = "Thinking",
            ThinkingExpanded = true,
            Thinking = "",
        };
        Bubbles.Add(_liveBubble);
        ResetStreamState();
        PersistChat();

        IsRunning = true;
        StatusText = AgentMode ? "Agent running…" : "Streaming…";
        _runCts = new CancellationTokenSource();
        StartPaceTimer();
        try
        {
            if (AgentMode)
            {
                await _runner.StartAsync(
                    _configService.AgentRoot,
                    GoalText.Trim(),
                    new AgentRunOptions
                    {
                        MaxSteps = RunMaxSteps,
                        DryRun = DryRun,
                        EmbeddingUrl = Config.EmbeddingUrl,
                        EmbeddingModel = SelectedEmbedModel,
                        Stream = true,
                    },
                    _runCts.Token);
                _streamFinished = true;
            }
            else
            {
                await RunStreamingChatAsync(_runCts.Token);
            }
        }
        catch (OperationCanceledException)
        {
            AppendLog("[cancelled]");
            _streamFinished = true;
            if (_liveBubble is not null)
            {
                _liveBubble.IsThinking = false;
                if (string.IsNullOrWhiteSpace(_liveBubble.Text) && string.IsNullOrWhiteSpace(_liveBubble.Thinking))
                    _liveBubble.Text = "Generation stopped.";
            }
            PersistChat();
        }
        catch (Exception ex)
        {
            AppendLog($"[error] {ex.Message}");
            FlashToast(ex.Message);
            _streamFinished = true;
            if (_liveBubble is not null)
            {
                _liveBubble.IsThinking = false;
                if (string.IsNullOrWhiteSpace(_liveBubble.Text))
                    _liveBubble.Text = ex.Message;
            }
            IsRunning = false;
            StatusText = "Error";
            PersistChat();
        }
    }

    private async Task RunStreamingChatAsync(CancellationToken ct)
    {
        // Same brain + octopus memory box the Python agent uses.
        var system = _brain.LoadBrain(forceReload: true);
        string boxJson;
        try
        {
            boxJson = await _brain.RecallOctopusBoxAsync(GoalText.Trim(), ct);
            App.Log($"octopus recall chars={boxJson.Length}");
        }
        catch (Exception ex)
        {
            boxJson = "{\"error\":\"recall_exception\",\"detail\":\"" + ex.Message.Replace("\"", "'") + "\"}";
            App.Log("octopus recall exception: " + ex.Message);
        }

        var history = new List<(string Role, string Content)> { ("system", system) };
        foreach (var b in Bubbles.TakeLast(10))
        {
            if (ReferenceEquals(b, _liveBubble)) continue;
            var body = b.Text;
            if (string.IsNullOrWhiteSpace(body) && !string.IsNullOrWhiteSpace(b.Thinking))
                body = b.Thinking;
            if (string.IsNullOrWhiteSpace(body)) continue;
            history.Add((b.IsAgent ? "assistant" : "user", body));
        }
        history.Add(("user", _brain.BuildChatUserMessage(GoalText, agentMode: false, octopusBoxJson: boxJson)));

        App.Log($"chat brain wired: system_chars={system.Length} history_msgs={history.Count} box_chars={boxJson.Length}");

        // Enqueue on background — UI drains at 16 tok/s (no per-token Invoke freeze).
        await _chatStream.StreamChatAsync(
            BaseUrl,
            string.IsNullOrWhiteSpace(SelectedChatModel) ? "pocket" : SelectedChatModel,
            history,
            EnqueueDisplayTokens,
            temperature: Math.Min(Temperature, 0.35),
            maxTokens: Math.Max(512, MaxTokens),
            ct: ct);

        _streamFinished = true;
    }

    private void ResetStreamState()
    {
        while (_displayQueue.TryDequeue(out _)) { }
        _streamBuf.Clear();
        _seenThinkOpen = false;
        _thinkClosed = false;
        _streamFinished = false;
    }

    private void StartPaceTimer()
    {
        _paceTimer?.Stop();
        _paceTimer = new DispatcherTimer(DispatcherPriority.Background)
        {
            // 16 tokens/sec ≈ 62.5ms per token
            Interval = TimeSpan.FromMilliseconds(1000.0 / StreamTokensPerSecond),
        };
        _paceTimer.Tick += PaceTimer_Tick;
        _paceTimer.Start();
    }

    private void StopPaceTimer()
    {
        if (_paceTimer is null) return;
        _paceTimer.Tick -= PaceTimer_Tick;
        _paceTimer.Stop();
        _paceTimer = null;
    }

    private void EnqueueDisplayTokens(string piece)
    {
        if (string.IsNullOrEmpty(piece)) return;
        // Rough tokenization for pacing: keep whitespace with following chunk.
        var i = 0;
        while (i < piece.Length)
        {
            var start = i;
            // Emit ~1 "token" (word or punctuation run) at a time.
            if (char.IsWhiteSpace(piece[i]))
            {
                while (i < piece.Length && char.IsWhiteSpace(piece[i])) i++;
                _displayQueue.Enqueue(piece[start..i]);
                continue;
            }
            if (char.IsLetterOrDigit(piece[i]) || piece[i] == '_' || piece[i] == '<' || piece[i] == '/')
            {
                while (i < piece.Length && (char.IsLetterOrDigit(piece[i]) || piece[i] is '_' or '<' or '/' or '>'))
                    i++;
                _displayQueue.Enqueue(piece[start..i]);
                continue;
            }
            _displayQueue.Enqueue(piece[i].ToString());
            i++;
        }
    }

    private void PaceTimer_Tick(object? sender, EventArgs e)
    {
        if (_displayQueue.TryDequeue(out var tok))
        {
            ApplyStreamToken(tok);
            return;
        }

        if (_streamFinished)
        {
            // Drain complete — keep Thought visible (don't auto-collapse; user asked to see thinking).
            if (_liveBubble is not null)
            {
                _liveBubble.IsThinking = false;
                if (_seenThinkOpen || !string.IsNullOrWhiteSpace(_liveBubble.Thinking))
                {
                    _liveBubble.ThinkingLabel = "Thought";
                    _liveBubble.ThinkingExpanded = true;
                }
                // If the model never produced an answer section, promote thinking to text too.
                if (string.IsNullOrWhiteSpace(_liveBubble.Text) && !string.IsNullOrWhiteSpace(_liveBubble.Thinking))
                {
                    var think = _liveBubble.Thinking.Trim();
                    // Prefer last paragraph as the visible answer when tags were missing.
                    var parts = think.Split(new[] { "\n\n" }, StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length >= 2)
                    {
                        _liveBubble.Thinking = string.Join("\n\n", parts.Take(parts.Length - 1)).Trim();
                        _liveBubble.Text = parts[^1].Trim();
                    }
                    else
                    {
                        _liveBubble.Text = think;
                    }
                }
            }
            StopPaceTimer();
            if (IsRunning)
            {
                IsRunning = false;
                StatusText = "Idle";
                PersistChat();
            }
        }
    }

    [RelayCommand]
    private void ClearChat()
    {
        Bubbles.Clear();
        Bubbles.Add(new ChatBubble("Kurama", "Chat cleared. History wiped for this machine only.", true));
        PersistChat();
        FlashToast("Chat cleared");
    }

    [RelayCommand]
    private void StopAgent()
    {
        _runCts?.Cancel();
        _runner.Stop();
        _streamFinished = true;
        while (_displayQueue.TryDequeue(out _)) { }
        StopPaceTimer();
        IsRunning = false;
        StatusText = "Stopped";
        if (_liveBubble is not null)
        {
            _liveBubble.IsThinking = false;
            if (string.IsNullOrWhiteSpace(_liveBubble.Text))
                _liveBubble.Text = "Generation stopped.";
        }
        FlashToast("Stopped");
        PersistChat();
    }

    [RelayCommand]
    private async Task RewardLastRunAsync()
    {
        if (IsRunning)
        {
            FlashToast("Wait for the current run to finish");
            return;
        }
        FlashToast("Rewarding last run…");
        try
        {
            var (code, output) = await _runner.RunRewardAsync(_configService.AgentRoot, note: "studio reward button");
            AppendLog(output);
            Bubbles.Add(new ChatBubble("Kurama", string.IsNullOrWhiteSpace(output) ? "Reward applied." : output, true));
            FlashToast(code == 0 ? "Reward saved — beliefs + lessons updated" : "Reward failed");
        }
        catch (Exception ex)
        {
            FlashToast($"Reward error: {ex.Message}");
        }
    }

    [RelayCommand]
    private void AutoAllocateResources()
    {
        // Mirror core/resources.py recommendations for ~this class of machine.
        // Full probe happens in Python on save/run; UI applies a sensible profile now.
        SelectedPreset = "auto";
        // Prefer large context for robust reasoning; Python refine on save/run.
        NumCtx = Math.Max(NumCtx, 32768);
        NumBatch = 512;
        NumGpu = 0;
        NumThread = Math.Max(2, Environment.ProcessorCount - 2);
        MaxTokens = 600;
        KeepAlive = "5m";
        TimeoutSec = 420;
        Warmup = true;
        FlashToast($"Auto resources: {NumThread} threads, ctx {NumCtx}, CPU-only");
    }

    private void OnRunExited(int code)
    {
        _streamFinished = true;
        // Let the 16 tok/s pace timer finish draining before flipping Idle.
        if (_displayQueue.IsEmpty)
        {
            IsRunning = false;
            StatusText = code == 0 ? "Idle" : $"Exited {code}";
            if (_liveBubble is not null)
            {
                _liveBubble.IsThinking = false;
                if (_liveBubble.HasThinking) _liveBubble.ThinkingLabel = "Thought";
                if (string.IsNullOrWhiteSpace(_liveBubble.Text))
                    _liveBubble.Text = code == 0 ? "Done." : $"Exited {code}";
            }
            PersistChat();
        }
    }

    private void PersistChat() => _chatHistory.Save(Bubbles);

    private void OnRunnerLine(string line)
    {
        if (line.StartsWith("[[stream]]", StringComparison.Ordinal))
        {
            var tok = line["[[stream]]".Length..].Replace("[[nl]]", "\n", StringComparison.Ordinal);
            EnqueueDisplayTokens(tok);
            return;
        }
        AppendLog(line);
    }

    private void ApplyStreamToken(string token)
    {
        if (_liveBubble is null)
        {
            _liveBubble = new ChatBubble("Kurama", "", true)
            {
                IsThinking = true,
                ThinkingLabel = "Thinking",
                ThinkingExpanded = true,
            };
            Bubbles.Add(_liveBubble);
        }

        _streamBuf.Append(token);
        RenderStreamBuffer();
    }

    private void RenderStreamBuffer()
    {
        if (_liveBubble is null) return;
        var buf = _streamBuf.ToString();

        // Always keep the Thinking panel visible while content is arriving.
        _liveBubble.ThinkingExpanded = true;

        var (openIdx, openLen) = FindThinkOpen(buf);
        var closeIdx = openIdx >= 0 ? FindThinkClose(buf, openIdx + openLen) : -1;

        if (openIdx >= 0)
        {
            _seenThinkOpen = true;
            var afterOpen = openIdx + openLen;

            if (closeIdx < 0)
            {
                _thinkClosed = false;
                _liveBubble.IsThinking = true;
                _liveBubble.ThinkingLabel = "Thinking";
                _liveBubble.Thinking = SafeSlice(buf, afterOpen, buf.Length);
                _liveBubble.Text = ""; // answer comes after </think>
                return;
            }

            _thinkClosed = true;
            var closeLen = buf.AsSpan(closeIdx).StartsWith("</thinking>", StringComparison.OrdinalIgnoreCase) ? 12 : 8;
            _liveBubble.Thinking = SafeSlice(buf, afterOpen, closeIdx).Trim();
            _liveBubble.IsThinking = false;
            _liveBubble.ThinkingLabel = "Thought";
            var afterClose = closeIdx + closeLen;
            _liveBubble.Text = StripJsonFence(SafeSlice(buf, afterClose, buf.Length).TrimStart());
            return;
        }

        // No <think> tags (common on tiny models): stream into Thinking first,
        // then split to answer on a blank line once we have enough text.
        _liveBubble.IsThinking = true;
        _liveBubble.ThinkingLabel = "Thinking";
        var split = buf.IndexOf("\n\n", StringComparison.Ordinal);
        if (!_thinkClosed && split > 40 && buf.Length - split > 12)
        {
            _seenThinkOpen = true;
            _thinkClosed = true;
            _liveBubble.Thinking = buf[..split].Trim();
            _liveBubble.IsThinking = false;
            _liveBubble.ThinkingLabel = "Thought";
            _liveBubble.Text = StripJsonFence(buf[(split + 2)..].TrimStart());
        }
        else if (_thinkClosed)
        {
            // Already split earlier — keep appending to answer.
            var prior = _liveBubble.Thinking ?? "";
            var idx = buf.IndexOf(prior, StringComparison.Ordinal);
            if (idx >= 0 && idx + prior.Length + 2 <= buf.Length)
                _liveBubble.Text = StripJsonFence(buf[(idx + prior.Length)..].TrimStart('\n', '\r', ' '));
            else
                _liveBubble.Text = StripJsonFence(buf);
            _liveBubble.IsThinking = false;
            _liveBubble.ThinkingLabel = "Thought";
        }
        else
        {
            _seenThinkOpen = true; // show panel even without tags
            _liveBubble.Thinking = buf;
            _liveBubble.Text = "";
        }
    }

    private static (int Idx, int Len) FindThinkOpen(string buf)
    {
        var a = buf.IndexOf("<think>", StringComparison.OrdinalIgnoreCase);
        if (a >= 0) return (a, 7);
        var b = buf.IndexOf("<thinking>", StringComparison.OrdinalIgnoreCase);
        if (b >= 0) return (b, 10);
        return (-1, 0);
    }

    private static int FindThinkClose(string buf, int start)
    {
        var a = buf.IndexOf("</think>", start, StringComparison.OrdinalIgnoreCase);
        var b = buf.IndexOf("</thinking>", start, StringComparison.OrdinalIgnoreCase);
        if (a < 0) return b;
        if (b < 0) return a;
        return Math.Min(a, b);
    }

    private static string SafeSlice(string s, int start, int end)
    {
        if (start < 0) start = 0;
        if (end > s.Length) end = s.Length;
        if (start >= end) return "";
        return s[start..end];
    }

    private static string StripJsonFence(string text)
    {
        // Prefer showing finish.summary if the model emitted an action JSON blob.
        var fence = text.IndexOf("```json", StringComparison.OrdinalIgnoreCase);
        if (fence < 0) return text;
        var visible = text[..fence].Trim();
        var jsonPart = text[(fence + 7)..];
        var end = jsonPart.IndexOf("```", StringComparison.Ordinal);
        if (end >= 0) jsonPart = jsonPart[..end];
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(jsonPart.Trim());
            if (doc.RootElement.TryGetProperty("finish", out var finish) &&
                finish.TryGetProperty("summary", out var summary))
            {
                var s = summary.GetString();
                if (!string.IsNullOrWhiteSpace(s))
                    return string.IsNullOrWhiteSpace(visible) ? s! : visible + "\n\n" + s;
            }
        }
        catch { /* leave raw */ }
        return string.IsNullOrWhiteSpace(visible) ? text : visible;
    }

    private void AppendLog(string line)
    {
        LogLines.Add(line);
        while (LogLines.Count > 2000) LogLines.RemoveAt(0);
    }

    private async void FlashToast(string message)
    {
        Toast = message;
        ShowToast = true;
        await Task.Delay(2600);
        ShowToast = false;
    }

    public SolidColorBrush AccentBrush
    {
        get
        {
            try
            {
                var c = (Color)ColorConverter.ConvertFromString(AccentHex)!;
                return new SolidColorBrush(c);
            }
            catch
            {
                return new SolidColorBrush(Color.FromRgb(0xF5, 0x9E, 0x0B));
            }
        }
    }
}

public partial class ChatBubble : ObservableObject
{
    public ChatBubble(string who, string text, bool isAgent)
    {
        Who = who;
        Text = text;
        IsAgent = isAgent;
    }

    public string Who { get; }
    public bool IsAgent { get; }

    [ObservableProperty] private string _text = "";
    [ObservableProperty] private string _thinking = "";
    [ObservableProperty] private bool _isThinking;
    [ObservableProperty] private bool _thinkingExpanded = true;
    [ObservableProperty] private string _thinkingLabel = "Thinking";

    public bool HasThinking => !string.IsNullOrWhiteSpace(Thinking) || IsThinking;

    partial void OnThinkingChanged(string value) => OnPropertyChanged(nameof(HasThinking));
    partial void OnIsThinkingChanged(bool value) => OnPropertyChanged(nameof(HasThinking));
}

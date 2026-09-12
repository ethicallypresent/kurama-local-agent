using System.Collections.ObjectModel;
using System.Text.Json.Nodes;
using KuramaStudio.Services;

namespace KuramaStudio.Models;

/// <summary>Editable mirror of brain/reasoning_config.json for the studio UI.</summary>
public sealed class AgentConfigModel
{
    public string ConfigPath { get; set; } = "";
    public string AgentRoot { get; set; } = "";

    // Connection / models
    public string BaseUrl { get; set; } = "http://localhost:11434/v1";
    public string Model { get; set; } = "pocket:latest";
    public string ModelPath { get; set; } = "models/pocket.gguf";
    public string FallbackModel { get; set; } = "pocket:latest";
    public string EmbeddingModel { get; set; } = "nomic-embed-text";
    public string EmbeddingUrl { get; set; } = "http://localhost:11434/v1";
    public string ApiKey { get; set; } = "sk-no-key-needed";

    // Generation
    public double Temperature { get; set; } = 0.2;
    public int MaxTokens { get; set; } = 1200;
    public int TimeoutSec { get; set; } = 300;
    public double TopP { get; set; } = 0.9;
    public int TopK { get; set; } = 40;
    public double RepeatPenalty { get; set; } = 1.1;
    public int Mirostat { get; set; }

    // Load / runtime
    public string LoadPreset { get; set; } = "cpu_optimal";
    public int NumCtx { get; set; } = 4096;
    public int NumBatch { get; set; } = 512;
    public int NumGpu { get; set; }
    public int NumThread { get; set; }
    public string KeepAlive { get; set; } = "10m";
    public bool Warmup { get; set; } = true;
    public bool DryRunOnNoServer { get; set; } = true;

    // Loop / safety
    public int MaxSteps { get; set; } = 50;
    public int MaxActionsPerRun { get; set; } = 200;
    public int MaxSkillDraftsPerRun { get; set; } = 3;
    public int ToolTopK { get; set; } = 8;
    public int MemoryTopK { get; set; } = 5;
    public int SkillTopK { get; set; } = 8;
    public bool SkillTestRequired { get; set; } = true;
    public bool RequireThinkBlock { get; set; } = true;
    public bool ThinkMustCiteSource { get; set; } = true;
    public bool CodeRunnerNetwork { get; set; }
    public bool LogRawTranscripts { get; set; }
    public bool MemoryPromotionRequiresReview { get; set; } = true;

    public ObservableCollection<string> FallbackTriggers { get; } = new();

    public static readonly IReadOnlyList<(string Id, string Label, string Hint)> LoadPresets =
    [
        ("auto", "Auto (this PC)", "Probe RAM/CPU/GPU and pick a safe profile"),
        ("cpu_optimal", "CPU Optimal", "Small context, CPU-only — best for 1B models on laptop CPUs"),
        ("balanced", "Balanced", "Medium context, GPU if available"),
        ("max_context", "Max Context", "Large context window — slower prefills"),
        ("fast_draft", "Fast Draft", "Tiny context + short replies — quick iteration"),
        ("custom", "Custom", "Manual knobs below"),
    ];

    public void ApplyPreset(string presetId)
    {
        LoadPreset = presetId;
        switch (presetId)
        {
            case "cpu_optimal":
                NumCtx = 4096; NumBatch = 256; NumGpu = 0; NumThread = 0;
                MaxTokens = 800; KeepAlive = "10m"; TimeoutSec = 300;
                break;
            case "balanced":
                NumCtx = 8192; NumBatch = 512; NumGpu = -1; NumThread = 0;
                MaxTokens = 1200; KeepAlive = "15m"; TimeoutSec = 240;
                break;
            case "max_context":
                NumCtx = 16384; NumBatch = 512; NumGpu = -1; NumThread = 0;
                MaxTokens = 1200; KeepAlive = "30m"; TimeoutSec = 420;
                break;
            case "fast_draft":
                NumCtx = 2048; NumBatch = 128; NumGpu = 0; NumThread = 0;
                MaxTokens = 400; KeepAlive = "5m"; TimeoutSec = 180;
                break;
        }
    }

    public static AgentConfigModel FromJson(string json, string path, string root)
    {
        var node = JsonNode.Parse(json)?.AsObject()
                   ?? throw new InvalidOperationException("Config is not a JSON object");
        var llm = node["llm"]?.AsObject() ?? new JsonObject();
        var llama = node["llama_server"]?.AsObject() ?? new JsonObject();
        var model = new AgentConfigModel
        {
            ConfigPath = path,
            AgentRoot = root,
            BaseUrl = llm["base_url"]?.GetValue<string>() ?? node["base_url"]?.GetValue<string>() ?? "http://localhost:11434/v1",
            Model = llm["model"]?.GetValue<string>() ?? node["model"]?.GetValue<string>() ?? "pocket:latest",
            ModelPath = llama["model_path"]?.GetValue<string>() ?? "models/pocket.gguf",
            FallbackModel = node["fallback_model"]?.GetValue<string>() ?? "pocket:latest",
            EmbeddingModel = llm["embedding_model"]?.GetValue<string>() ?? "nomic-embed-text",
            EmbeddingUrl = llm["embedding_url"]?.GetValue<string>() ?? "http://localhost:11434/v1",
            ApiKey = llm["api_key"]?.GetValue<string>() ?? "sk-no-key-needed",
            Temperature = llm["temperature"]?.GetValue<double>() ?? node["temperature"]?.GetValue<double>() ?? 0.2,
            MaxTokens = llm["max_tokens"]?.GetValue<int>() ?? node["max_tokens"]?.GetValue<int>() ?? 1200,
            TimeoutSec = llm["timeout_sec"]?.GetValue<int>() ?? 300,
            TopP = llm["top_p"]?.GetValue<double>() ?? 0.9,
            TopK = llm["top_k"]?.GetValue<int>() ?? 40,
            RepeatPenalty = llm["repeat_penalty"]?.GetValue<double>() ?? 1.1,
            Mirostat = llm["mirostat"]?.GetValue<int>() ?? 0,
            LoadPreset = llm["load_preset"]?.GetValue<string>() ?? "cpu_optimal",
            NumCtx = llm["num_ctx"]?.GetValue<int>() ?? 4096,
            NumBatch = llm["num_batch"]?.GetValue<int>() ?? 512,
            NumGpu = llm["num_gpu"]?.GetValue<int>() ?? 0,
            NumThread = llm["num_thread"]?.GetValue<int>() ?? 0,
            KeepAlive = llm["keep_alive"]?.GetValue<string>() ?? "10m",
            Warmup = llm["warmup"]?.GetValue<bool>() ?? true,
            DryRunOnNoServer = llm["dry_run_on_no_server"]?.GetValue<bool>() ?? true,
            MaxSteps = node["max_steps"]?.GetValue<int>() ?? 50,
            MaxActionsPerRun = node["max_actions_per_run"]?.GetValue<int>() ?? 200,
            MaxSkillDraftsPerRun = node["max_skill_drafts_per_run"]?.GetValue<int>() ?? 3,
            ToolTopK = node["tool_topk"]?.GetValue<int>() ?? 8,
            MemoryTopK = node["memory_topk"]?.GetValue<int>() ?? 5,
            SkillTopK = node["skill_topk"]?.GetValue<int>() ?? 8,
            SkillTestRequired = node["skill_test_required_before_use"]?.GetValue<bool>() ?? true,
            RequireThinkBlock = node["require_think_block"]?.GetValue<bool>() ?? true,
            ThinkMustCiteSource = node["think_block_must_cite_source"]?.GetValue<bool>() ?? true,
            CodeRunnerNetwork = node["code_runner_network_enabled"]?.GetValue<bool>() ?? false,
            LogRawTranscripts = node["log_raw_transcripts"]?.GetValue<bool>() ?? false,
            MemoryPromotionRequiresReview = node["memory_promotion_requires_review"]?.GetValue<bool>() ?? true,
        };

        if (node["fallback_triggers"] is JsonArray arr)
        {
            foreach (var item in arr)
            {
                var s = item?.GetValue<string>();
                if (!string.IsNullOrWhiteSpace(s)) model.FallbackTriggers.Add(s);
            }
        }
        return model;
    }

    public string MergeIntoExistingJson(string existingJson)
    {
        var root = JsonNode.Parse(existingJson)?.AsObject()
                   ?? throw new InvalidOperationException("Config is not a JSON object");
        var llm = root["llm"]?.AsObject() ?? new JsonObject();
        root["llm"] = llm;

        root["model"] = Model;
        root["base_url"] = BaseUrl;
        root["fallback_model"] = FallbackModel;
        root["temperature"] = Temperature;
        root["max_tokens"] = MaxTokens;
        root["max_steps"] = MaxSteps;
        root["max_actions_per_run"] = MaxActionsPerRun;
        root["max_skill_drafts_per_run"] = MaxSkillDraftsPerRun;
        root["tool_topk"] = ToolTopK;
        root["memory_topk"] = MemoryTopK;
        root["skill_topk"] = SkillTopK;
        root["skill_test_required_before_use"] = SkillTestRequired;
        root["require_think_block"] = RequireThinkBlock;
        root["think_block_must_cite_source"] = ThinkMustCiteSource;
        root["code_runner_network_enabled"] = CodeRunnerNetwork;
        root["log_raw_transcripts"] = LogRawTranscripts;
        root["memory_promotion_requires_review"] = MemoryPromotionRequiresReview;

        llm["backend"] = "llamacpp";
        llm["base_url"] = BaseUrl;
        llm["model"] = Model;
        llm["api_key"] = ApiKey;
        llm["temperature"] = Temperature;
        llm["max_tokens"] = MaxTokens;
        llm["timeout_sec"] = TimeoutSec;
        llm["dry_run_on_no_server"] = DryRunOnNoServer;
        llm["warmup"] = Warmup;
        llm["keep_alive"] = KeepAlive;
        llm["embedding_model"] = EmbeddingModel;
        llm["embedding_url"] = EmbeddingUrl;
        llm["num_ctx"] = NumCtx;
        llm["num_batch"] = NumBatch;
        llm["num_gpu"] = NumGpu;
        llm["num_thread"] = NumThread;
        llm["top_p"] = TopP;
        llm["top_k"] = TopK;
        llm["repeat_penalty"] = RepeatPenalty;
        llm["mirostat"] = Mirostat;
        llm["load_preset"] = LoadPreset;

        var llama = root["llama_server"]?.AsObject() ?? new JsonObject();
        root["llama_server"] = llama;
        llama["model_path"] = ModelPath;
        llama["auto_start"] = llama["auto_start"] is JsonValue av ? av : true;
        llama["host"] = llama["host"]?.GetValue<string>() ?? "127.0.0.1";
        llama["port"] = llama["port"] is JsonValue pv ? pv : 8080;
        llama["jinja"] = llama["jinja"] is JsonValue jv ? jv : true;

        var triggers = new JsonArray();
        foreach (var t in FallbackTriggers) triggers.Add(t);
        if (triggers.Count > 0) root["fallback_triggers"] = triggers;

        return ConfigService.PrettyJson(root);
    }
}

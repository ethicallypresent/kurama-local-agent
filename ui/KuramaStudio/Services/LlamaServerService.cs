using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;

namespace KuramaStudio.Services;

/// <summary>
/// Starts/stops llama-server so KuramaStudio.exe can launch the full stack.
/// Looks for llama-server next to the exe (portable), then on PATH / WinGet.
/// </summary>
public sealed class LlamaServerService : IDisposable
{
    private Process? _proc;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(3) };

    public int Port { get; private set; } = 8080;
    public string Host { get; private set; } = "127.0.0.1";
    public string BaseUrl => $"http://{Host}:{Port}/v1";
    public bool IsRunning => _proc is { HasExited: false };
    public string? LastError { get; private set; }
    public string? BinaryPath { get; private set; }
    public string? ModelPath { get; private set; }

    public event Action<string>? Log;

    public static string? FindBinary(string agentRoot)
    {
        var candidates = new List<string>();

        // Portable layout: next to exe, or under tools/bin
        var baseDir = AppContext.BaseDirectory;
        candidates.Add(Path.Combine(baseDir, "llama-server.exe"));
        candidates.Add(Path.Combine(baseDir, "bin", "llama-server.exe"));
        candidates.Add(Path.Combine(agentRoot, "tools", "bin", "llama-server.exe"));
        candidates.Add(Path.Combine(agentRoot, "bin", "llama-server.exe"));

        // PATH
        var pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var dir in pathEnv.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            try
            {
                candidates.Add(Path.Combine(dir.Trim('"'), "llama-server.exe"));
            }
            catch { /* ignore bad PATH entries */ }
        }

        // Common WinGet install location
        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var wingetRoot = Path.Combine(local, "Microsoft", "WinGet", "Packages");
        if (Directory.Exists(wingetRoot))
        {
            foreach (var hit in Directory.EnumerateFiles(wingetRoot, "llama-server.exe", SearchOption.AllDirectories)
                         .Take(5))
                candidates.Add(hit);
        }

        return candidates.FirstOrDefault(File.Exists);
    }

    public static string ResolveModelPath(string agentRoot, string? configured)
    {
        if (!string.IsNullOrWhiteSpace(configured))
        {
            var p = Path.IsPathRooted(configured) ? configured : Path.Combine(agentRoot, configured);
            if (File.Exists(p)) return p;
        }
        var fallback = Path.Combine(agentRoot, "models", "pocket.gguf");
        return fallback;
    }

    public async Task<bool> EnsureRunningAsync(
        string agentRoot,
        LlamaServerOptions options,
        CancellationToken ct = default,
        bool forceRestart = false)
    {
        Host = string.IsNullOrWhiteSpace(options.Host) ? "127.0.0.1" : options.Host;
        Port = options.Port > 0 ? options.Port : 8080;

        var wanted = ResolveModelPath(agentRoot, options.ModelPath);
        var sameModel = ModelPath is string current
                        && File.Exists(wanted)
                        && string.Equals(Path.GetFullPath(current), Path.GetFullPath(wanted), StringComparison.OrdinalIgnoreCase);
        var healthy = await IsHealthyAsync(ct);

        if (healthy && !forceRestart && (sameModel || ModelPath is null))
        {
            if (File.Exists(wanted))
                ModelPath = wanted;
            Log?.Invoke($"llama-server already healthy at {BaseUrl} ({Path.GetFileName(ModelPath)})");
            return true;
        }

        if (forceRestart || (healthy && !sameModel))
        {
            Log?.Invoke($"Stopping llama-server to load {Path.GetFileName(wanted)}");
            var freed = await StopAndWaitAsync(ct);
            if (!freed)
            {
                LastError = $"Could not free port {Port}. Stop llama-server and try again.";
                Log?.Invoke(LastError);
                return false;
            }
        }

        BinaryPath = FindBinary(agentRoot);
        if (BinaryPath is null)
        {
            LastError = "llama-server.exe not found. Install llama.cpp or place it in tools/bin/.";
            Log?.Invoke(LastError);
            return false;
        }

        ModelPath = ResolveModelPath(agentRoot, options.ModelPath);
        if (!File.Exists(ModelPath))
        {
            LastError = $"Model GGUF not found: {ModelPath}";
            Log?.Invoke(LastError);
            return false;
        }

        try
        {
            // WorkingDirectory MUST be the folder containing llama-server.exe and
            // its sibling DLLs (llama-server-impl.dll etc). The WinGet stub is ~9KB
            // and loads impl DLLs from cwd/exe dir — starting from agent root crashes.
            var binDir = Path.GetDirectoryName(BinaryPath) ?? agentRoot;
            var psi = new ProcessStartInfo
            {
                FileName = BinaryPath,
                WorkingDirectory = binDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            // Resource-aware load flags (absolute model path — cwd is binDir)
            psi.ArgumentList.Add("-m");
            psi.ArgumentList.Add(Path.GetFullPath(ModelPath));
            psi.ArgumentList.Add("--alias");
            psi.ArgumentList.Add(string.IsNullOrWhiteSpace(options.Alias) ? "pocket" : options.Alias);
            psi.ArgumentList.Add("--host");
            psi.ArgumentList.Add(Host);
            psi.ArgumentList.Add("--port");
            psi.ArgumentList.Add(Port.ToString());
            psi.ArgumentList.Add("-c");
            psi.ArgumentList.Add(Math.Max(512, options.NumCtx).ToString());
            psi.ArgumentList.Add("-t");
            psi.ArgumentList.Add(Math.Max(1, options.NumThread).ToString());
            psi.ArgumentList.Add("-b");
            psi.ArgumentList.Add(Math.Max(32, options.NumBatch).ToString());
            psi.ArgumentList.Add("-ngl");
            psi.ArgumentList.Add(Math.Max(0, options.NumGpu).ToString());
            psi.ArgumentList.Add("--parallel");
            psi.ArgumentList.Add("1");
            if (options.Jinja) psi.ArgumentList.Add("--jinja");

            _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
            _proc.OutputDataReceived += (_, e) => { if (e.Data != null) Log?.Invoke(e.Data); };
            _proc.ErrorDataReceived += (_, e) => { if (e.Data != null) Log?.Invoke(e.Data); };
            if (!_proc.Start())
            {
                LastError = "Failed to start llama-server process.";
                return false;
            }
            _proc.BeginOutputReadLine();
            _proc.BeginErrorReadLine();
            Log?.Invoke($"Started llama-server pid={_proc.Id} model={Path.GetFileName(ModelPath)} ctx={options.NumCtx} threads={options.NumThread}");
        }
        catch (Exception ex)
        {
            LastError = ex.Message;
            Log?.Invoke($"llama-server start failed: {ex.Message}");
            return false;
        }

        // Wait until /v1/models answers (cold load can take a bit on CPU)
        var deadline = DateTime.UtcNow.AddSeconds(Math.Max(30, options.StartupTimeoutSec));
        while (DateTime.UtcNow < deadline)
        {
            ct.ThrowIfCancellationRequested();
            if (_proc is { HasExited: true })
            {
                LastError = $"llama-server exited early (code {_proc.ExitCode}).";
                Log?.Invoke(LastError);
                return false;
            }
            if (await IsHealthyAsync(ct))
            {
                Log?.Invoke($"llama-server ready at {BaseUrl}");
                return true;
            }
            await Task.Delay(750, ct);
        }

        LastError = "Timed out waiting for llama-server to become healthy.";
        Log?.Invoke(LastError);
        return false;
    }

    public async Task<bool> IsHealthyAsync(CancellationToken ct = default)
    {
        try
        {
            using var resp = await _http.GetAsync($"http://{Host}:{Port}/v1/models", ct);
            return resp.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    public async Task<IReadOnlyList<string>> ListModelsAsync(CancellationToken ct = default)
    {
        try
        {
            using var resp = await _http.GetAsync($"http://{Host}:{Port}/v1/models", ct);
            resp.EnsureSuccessStatusCode();
            await using var stream = await resp.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
            var list = new List<string>();
            if (doc.RootElement.TryGetProperty("data", out var data))
            {
                foreach (var m in data.EnumerateArray())
                {
                    if (m.TryGetProperty("id", out var id))
                        list.Add(id.GetString() ?? "");
                }
            }
            return list.Where(s => !string.IsNullOrWhiteSpace(s)).ToList();
        }
        catch
        {
            return Array.Empty<string>();
        }
    }

    public void Stop()
    {
        try
        {
            if (_proc is { HasExited: false })
            {
                Log?.Invoke($"Stopping llama-server pid={_proc.Id}");
                _proc.Kill(entireProcessTree: true);
                _proc.WaitForExit(4000);
            }
        }
        catch (Exception ex)
        {
            Log?.Invoke($"Stop tracked process: {ex.Message}");
        }
        finally
        {
            _proc?.Dispose();
            _proc = null;
        }

        KillByName("llama-server");
        KillByName("llama-server-impl");
        KillListenersOnPort(Port);
    }

    /// <summary>Kill every llama-server we can see and wait until :port is free.</summary>
    public async Task<bool> StopAndWaitAsync(CancellationToken ct = default, int timeoutMs = 12000)
    {
        Stop();
        var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        while (DateTime.UtcNow < deadline)
        {
            ct.ThrowIfCancellationRequested();
            if (!await IsHealthyAsync(ct))
            {
                await Task.Delay(400, ct); // let the OS release the bind
                if (!await IsHealthyAsync(ct))
                    return true;
            }
            KillByName("llama-server");
            KillListenersOnPort(Port);
            await Task.Delay(250, ct);
        }
        return !await IsHealthyAsync(ct);
    }

    private void KillByName(string processName)
    {
        Process[]? procs = null;
        try
        {
            procs = Process.GetProcessesByName(processName);
            foreach (var p in procs)
            {
                try
                {
                    if (p.HasExited) continue;
                    Log?.Invoke($"Killing {processName} pid={p.Id}");
                    p.Kill(entireProcessTree: true);
                    p.WaitForExit(3000);
                }
                catch (Exception ex)
                {
                    Log?.Invoke($"Kill {processName} pid={p.Id}: {ex.Message}");
                }
            }
        }
        catch (Exception ex)
        {
            Log?.Invoke($"KillByName {processName}: {ex.Message}");
        }
        finally
        {
            if (procs is not null)
            {
                foreach (var p in procs) p.Dispose();
            }
        }
    }

    private void KillListenersOnPort(int port)
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "netstat",
                Arguments = "-ano",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true,
            };
            using var p = Process.Start(psi);
            if (p is null) return;
            var output = p.StandardOutput.ReadToEnd();
            if (!p.WaitForExit(4000)) return;

            var needle = $":{port}";
            foreach (var raw in output.Split('\n'))
            {
                var line = raw.Trim();
                if (line.Length == 0) continue;
                if (!line.Contains(needle, StringComparison.Ordinal)) continue;
                if (!line.Contains("LISTENING", StringComparison.OrdinalIgnoreCase)) continue;
                var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length == 0 || !int.TryParse(parts[^1], out var pid) || pid <= 0)
                    continue;
                if (pid == Environment.ProcessId) continue;
                try
                {
                    using var victim = Process.GetProcessById(pid);
                    Log?.Invoke($"Killing pid={pid} ({victim.ProcessName}) listening on :{port}");
                    victim.Kill(entireProcessTree: true);
                    victim.WaitForExit(3000);
                }
                catch (Exception ex)
                {
                    Log?.Invoke($"Kill pid={pid} on :{port}: {ex.Message}");
                }
            }
        }
        catch (Exception ex)
        {
            Log?.Invoke($"KillListenersOnPort: {ex.Message}");
        }
    }

    public void Dispose()
    {
        Stop();
        _http.Dispose();
    }
}

public sealed class LlamaServerOptions
{
    public bool AutoStart { get; init; } = true;
    public string Host { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 8080;
    public string ModelPath { get; init; } = "models/pocket.gguf";
    public string Alias { get; init; } = "pocket";
    public int NumCtx { get; init; } = 2048;
    public int NumThread { get; init; } = 6;
    public int NumBatch { get; init; } = 128;
    public int NumGpu { get; init; }
    public bool Jinja { get; init; } = true;
    public int StartupTimeoutSec { get; init; } = 120;
}

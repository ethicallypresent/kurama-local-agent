using System.Diagnostics;
using System.Text;

namespace KuramaStudio.Services;

public sealed class AgentRunner
{
    private Process? _proc;

    public bool IsRunning => _proc is { HasExited: false };

    public event Action<string>? LineReceived;
    public event Action<int>? Exited;

    public async Task StartAsync(
        string agentRoot,
        string goal,
        AgentRunOptions options,
        CancellationToken ct = default)
    {
        if (IsRunning) throw new InvalidOperationException("A run is already in progress.");

        var psi = new ProcessStartInfo
        {
            FileName = "python",
            WorkingDirectory = agentRoot,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add("main.py");
        if (options.MaxSteps is int ms)
        {
            psi.ArgumentList.Add("--max-steps");
            psi.ArgumentList.Add(ms.ToString());
        }
        if (options.DryRun) psi.ArgumentList.Add("--dry-run");
        if (options.Stream) psi.ArgumentList.Add("--stream");
        else psi.ArgumentList.Add("--no-stream");
        psi.ArgumentList.Add(goal);

        psi.Environment["LLAMA_EMBED_URL"] = options.EmbeddingUrl;
        psi.Environment["LLAMA_EMBED_MODEL"] = options.EmbeddingModel;
        psi.Environment["PYTHONUNBUFFERED"] = "1";

        _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        _proc.OutputDataReceived += (_, e) => { if (e.Data != null) LineReceived?.Invoke(e.Data); };
        _proc.ErrorDataReceived += (_, e) => { if (e.Data != null) LineReceived?.Invoke(e.Data); };
        _proc.Exited += (_, _) =>
        {
            var code = _proc?.ExitCode ?? -1;
            Exited?.Invoke(code);
        };

        if (!_proc.Start()) throw new InvalidOperationException("Failed to start python.");
        _proc.BeginOutputReadLine();
        _proc.BeginErrorReadLine();

        await Task.Run(() =>
        {
            while (!_proc.HasExited)
            {
                ct.ThrowIfCancellationRequested();
                Thread.Sleep(100);
            }
        }, ct);
    }

    public void Stop()
    {
        try
        {
            if (_proc is { HasExited: false })
            {
                _proc.Kill(entireProcessTree: true);
            }
        }
        catch { /* ignore */ }
    }

    /// <summary>Run reflection reward pass separately from chat.</summary>
    public async Task<(int ExitCode, string Output)> RunRewardAsync(
        string agentRoot,
        string note = "",
        CancellationToken ct = default)
    {
        var psi = new ProcessStartInfo
        {
            FileName = "python",
            WorkingDirectory = agentRoot,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add("-m");
        psi.ArgumentList.Add("core.reflection");
        psi.ArgumentList.Add("--reward");
        if (!string.IsNullOrWhiteSpace(note))
        {
            psi.ArgumentList.Add("--note");
            psi.ArgumentList.Add(note);
        }
        psi.Environment["PYTHONUNBUFFERED"] = "1";

        using var proc = new Process { StartInfo = psi };
        if (!proc.Start()) throw new InvalidOperationException("Failed to start reward process.");
        var stdout = await proc.StandardOutput.ReadToEndAsync(ct);
        var stderr = await proc.StandardError.ReadToEndAsync(ct);
        await proc.WaitForExitAsync(ct);
        var combined = string.IsNullOrWhiteSpace(stderr) ? stdout : stdout + "\n" + stderr;
        return (proc.ExitCode, combined.Trim());
    }
}

public sealed class AgentRunOptions
{
    public int? MaxSteps { get; init; }
    public bool DryRun { get; init; }
    public bool Stream { get; init; } = true;
    public string EmbeddingUrl { get; init; } = "http://localhost:11434/v1";
    public string EmbeddingModel { get; init; } = "nomic-embed-text";
}

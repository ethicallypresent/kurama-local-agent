using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json.Nodes;

namespace KuramaStudio.Services;

/// <summary>
/// Loads the same brain the Python agent uses:
/// brain/system_prompt.md + brain/constitution.md (+ optional reasoning lenses).
/// </summary>
public sealed class BrainService
{
    private readonly string _agentRoot;
    private string? _cached;
    private DateTime _cachedUtc = DateTime.MinValue;

    public BrainService(string agentRoot) => _agentRoot = agentRoot;

    public string BrainDir => Path.Combine(_agentRoot, "brain");
    public string SystemPromptPath => Path.Combine(BrainDir, "system_prompt.md");
    public string ConstitutionPath => Path.Combine(BrainDir, "constitution.md");

    public bool LooksValid =>
        File.Exists(SystemPromptPath) && File.Exists(ConstitutionPath);

    /// <summary>
    /// Compact projection of the Python Brain organ (core.brain.Brain).
    /// Concatenation shape is a contract: identity + "--- Constitution (binding)" + law.
    /// Canonical law lives in *.full.md; this loader keeps the compact files so
    /// Studio's context window matches a small local model.
    /// </summary>
    public string LoadBrain(bool forceReload = false)
    {
        var stamp = MaxWriteUtc(SystemPromptPath, ConstitutionPath);
        if (!forceReload && _cached is not null && stamp <= _cachedUtc)
            return _cached;

        if (!LooksValid)
            throw new FileNotFoundException(
                $"Brain files missing under {BrainDir}. Expected system_prompt.md and constitution.md.");

        var system = File.ReadAllText(SystemPromptPath, Encoding.UTF8);
        var constitution = File.ReadAllText(ConstitutionPath, Encoding.UTF8);
        _cached = system + "\n\n---\n# Constitution (binding)\n\n" + constitution;
        _cachedUtc = stamp;
        return _cached;
    }

    /// <summary>Compact six-hats / mental-model scaffold (mirrors core.reasoning).</summary>
    public static string BuildReasoningScaffold(string goal, bool agentMode)
    {
        var g = (goal ?? "").ToLowerInvariant();
        var lenses = new List<string>
        {
            "Blue hat (process): where are we in the task, what happens next?",
            "Intent: what does the user most likely want if the ask is vague?",
        };
        if (agentMode)
        {
            lenses.Add("White hat (facts): what do we know vs need to observe with tools?");
            lenses.Add("Black hat (caution): risks, irreversible mistakes, safety.");
            lenses.Add("Simplest sufficient plan: smallest useful next action.");
            if (g.Contains("create") || g.Contains("design") || g.Contains("idea") || g.Contains("improve"))
                lenses.Add("Green hat (creative): alternatives worth considering.");
            if (g.Contains("fix") || g.Contains("bug") || g.Contains("delete") || g.Contains("break"))
                lenses.Add("Pre-mortem / inversion: how would this fail?");
        }
        else
        {
            lenses.Add("Red hat (feelings): tone and confidence.");
            lenses.Add("Yellow hat (benefit): what makes the answer useful?");
            lenses.Add("Robust answer: explain enough to be clear, not a one-liner.");
        }

        var sb = new StringBuilder();
        sb.AppendLine("REASONING_LENSES for this turn — use each briefly inside <think>, then answer/act:");
        for (var i = 0; i < lenses.Count; i++)
            sb.AppendLine($"{i + 1}. {lenses[i]}");
        sb.Append("Always open with <think> and close with </think> before the user-visible answer. Do not skip thinking.");
        return sb.ToString();
    }

    public string BuildChatUserMessage(string userText, bool agentMode, string? octopusBoxJson = null)
    {
        var mode = agentMode ? "action" : "chat";
        var scaffold = BuildReasoningScaffold(userText, agentMode);
        var box = string.IsNullOrWhiteSpace(octopusBoxJson)
            ? "{\"note\":\"memory recall unavailable this turn\"}"
            : octopusBoxJson.Trim();
        return (
            $"{scaffold}\n\nMode hint: {mode}.\n" +
            "You are an octopus — reach into OCTOPUS_BOX (memory DB, beliefs, skills, tools) " +
            "before answering. Mention what you used inside <think>.\n\n" +
            $"OCTOPUS_BOX:\n{box}\n\n" +
            $"User message:\n{userText.Trim()}"
        );
    }

    /// <summary>Call Python core.context_box so chat uses the same SQLite memory as the agent.</summary>
    public async Task<string> RecallOctopusBoxAsync(string query, CancellationToken ct = default)
    {
        var psi = new ProcessStartInfo
        {
            FileName = "python",
            WorkingDirectory = _agentRoot,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add("-m");
        psi.ArgumentList.Add("core.context_box");
        psi.ArgumentList.Add(query);
        psi.ArgumentList.Add("--topk");
        psi.ArgumentList.Add("10");
        psi.Environment["PYTHONUNBUFFERED"] = "1";

        using var proc = new Process { StartInfo = psi };
        if (!proc.Start()) return "{\"error\":\"failed to start recall\"}";
        var stdoutTask = proc.StandardOutput.ReadToEndAsync(ct);
        var stderrTask = proc.StandardError.ReadToEndAsync(ct);
        await proc.WaitForExitAsync(ct);
        var stdout = (await stdoutTask).Trim();
        var stderr = (await stderrTask).Trim();
        if (proc.ExitCode != 0 || string.IsNullOrWhiteSpace(stdout))
        {
            App.Log("octopus recall failed: " + stderr);
            return "{\"error\":\"recall_failed\",\"detail\":" + JsonValue.Create(stderr)?.ToJsonString() + "}";
        }
        return stdout;
    }

    private static DateTime MaxWriteUtc(params string[] paths)
    {
        var max = DateTime.MinValue;
        foreach (var p in paths)
        {
            if (!File.Exists(p)) continue;
            var t = File.GetLastWriteTimeUtc(p);
            if (t > max) max = t;
        }
        return max;
    }
}

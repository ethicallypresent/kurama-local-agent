using KuramaStudio.Models;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace KuramaStudio.Services;

public sealed class ConfigService
{
    public string AgentRoot { get; }
    public string ConfigPath => Path.Combine(AgentRoot, "brain", "reasoning_config.json");

    public ConfigService(string? agentRoot = null)
    {
        AgentRoot = agentRoot ?? DiscoverAgentRoot();
    }

    /// <summary>Pretty-print a JsonNode without JsonSerializerOptions (single-file safe).</summary>
    public static string PrettyJson(JsonNode node)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions { Indented = true }))
        {
            node.WriteTo(writer);
        }
        return Encoding.UTF8.GetString(stream.ToArray());
    }

    public static string DiscoverAgentRoot()
    {
        // Studio lives in <root>/ui/KuramaStudio — walk up until brain+core exist.
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir != null)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "brain")) &&
                Directory.Exists(Path.Combine(dir.FullName, "core")))
                return dir.FullName;
            dir = dir.Parent;
        }

        var cwd = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (cwd != null)
        {
            if (Directory.Exists(Path.Combine(cwd.FullName, "brain")) &&
                Directory.Exists(Path.Combine(cwd.FullName, "core")))
                return cwd.FullName;
            cwd = cwd.Parent;
        }

        throw new DirectoryNotFoundException("Could not locate Kurama agent root (brain/ + core/).");
    }

    public AgentConfigModel Load()
    {
        var json = File.ReadAllText(ConfigPath);
        return AgentConfigModel.FromJson(json, ConfigPath, AgentRoot);
    }

    public void Save(AgentConfigModel model)
    {
        var existing = File.ReadAllText(ConfigPath);
        var merged = model.MergeIntoExistingJson(existing);
        File.WriteAllText(ConfigPath, merged.TrimEnd() + Environment.NewLine);
    }
}

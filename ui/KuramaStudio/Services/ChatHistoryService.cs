using System.Collections.ObjectModel;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using KuramaStudio.ViewModels;

namespace KuramaStudio.Services;

/// <summary>Persists chat bubbles for the local user only (db/chat_history.json).</summary>
public sealed class ChatHistoryService
{
    private readonly string _path;

    public ChatHistoryService(string agentRoot)
    {
        var db = Path.Combine(agentRoot, "db");
        Directory.CreateDirectory(db);
        _path = Path.Combine(db, "chat_history.json");
    }

    public void LoadInto(ObservableCollection<ChatBubble> bubbles)
    {
        bubbles.Clear();
        if (!File.Exists(_path)) return;
        try
        {
            var root = JsonNode.Parse(File.ReadAllText(_path))?.AsArray();
            if (root is null) return;
            foreach (var item in root)
            {
                if (item is not JsonObject o) continue;
                var who = o["who"]?.GetValue<string>() ?? "";
                var text = o["text"]?.GetValue<string>() ?? "";
                var thinking = o["thinking"]?.GetValue<string>() ?? "";
                var isAgent = o["isAgent"]?.GetValue<bool>() ?? false;
                if (string.IsNullOrWhiteSpace(text) && string.IsNullOrWhiteSpace(thinking)) continue;
                bubbles.Add(new ChatBubble(who, text, isAgent)
                {
                    Thinking = thinking,
                    ThinkingLabel = string.IsNullOrWhiteSpace(thinking) ? "Thinking" : "Thought",
                    ThinkingExpanded = false,
                    IsThinking = false,
                });
            }
        }
        catch (Exception ex)
        {
            App.Log("chat history load failed: " + ex.Message);
        }
    }

    public void Save(IEnumerable<ChatBubble> bubbles)
    {
        try
        {
            var arr = new JsonArray();
            foreach (var b in bubbles.TakeLast(500))
            {
                arr.Add(new JsonObject
                {
                    ["who"] = b.Who,
                    ["text"] = b.Text,
                    ["thinking"] = b.Thinking,
                    ["isAgent"] = b.IsAgent,
                    ["at"] = DateTime.UtcNow.ToString("o"),
                });
            }
            using var stream = new MemoryStream();
            using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions { Indented = true }))
            {
                arr.WriteTo(writer);
            }
            File.WriteAllText(_path, Encoding.UTF8.GetString(stream.ToArray()) + Environment.NewLine);
        }
        catch (Exception ex)
        {
            App.Log("chat history save failed: " + ex.Message);
        }
    }
}

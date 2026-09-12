using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace KuramaStudio.Services;

/// <summary>OpenAI-compatible SSE chat streaming against local llama-server.</summary>
public sealed class ChatStreamService
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromMinutes(10) };

    public async Task StreamChatAsync(
        string baseUrl,
        string model,
        IReadOnlyList<(string Role, string Content)> messages,
        Action<string> onToken,
        double temperature = 0.4,
        int maxTokens = 1024,
        CancellationToken ct = default)
    {
        var arr = new JsonArray();
        foreach (var (role, content) in messages)
        {
            arr.Add(new JsonObject
            {
                ["role"] = role,
                ["content"] = content,
            });
        }
        var payload = new JsonObject
        {
            ["model"] = model,
            ["messages"] = arr,
            ["temperature"] = temperature,
            ["max_tokens"] = maxTokens,
            ["stream"] = true,
        };

        using var req = new HttpRequestMessage(HttpMethod.Post, baseUrl.TrimEnd('/') + "/chat/completions")
        {
            Content = new StringContent(payload.ToJsonString(), Encoding.UTF8, "application/json"),
        };
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));

        using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        if (!resp.IsSuccessStatusCode)
        {
            var err = await resp.Content.ReadAsStringAsync(ct);
            throw new InvalidOperationException($"Chat stream HTTP {(int)resp.StatusCode}: {err}");
        }

        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream);
        while (!reader.EndOfStream)
        {
            ct.ThrowIfCancellationRequested();
            var line = await reader.ReadLineAsync(ct);
            if (string.IsNullOrWhiteSpace(line)) continue;
            if (!line.StartsWith("data:", StringComparison.Ordinal)) continue;
            var data = line["data:".Length..].Trim();
            if (data == "[DONE]") break;
            try
            {
                using var doc = JsonDocument.Parse(data);
                var root = doc.RootElement;
                if (!root.TryGetProperty("choices", out var choices) || choices.GetArrayLength() == 0)
                    continue;
                var delta = choices[0].GetProperty("delta");
                if (delta.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.String)
                {
                    var tok = content.GetString();
                    if (!string.IsNullOrEmpty(tok)) onToken(tok!);
                }
            }
            catch (JsonException)
            {
                // skip malformed SSE chunks
            }
        }
    }
}

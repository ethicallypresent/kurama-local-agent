using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace KuramaStudio.Services;

public sealed class OllamaModelInfo
{
    public string Name { get; init; } = "";
    public string Family { get; init; } = "";
    public string ParameterSize { get; init; } = "";
    public long Size { get; init; }
    public bool IsEmbedding { get; init; }
    public override string ToString() => Name;
}

public sealed class OllamaService : IDisposable
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(8) };

    public string HostFromBaseUrl(string baseUrl)
    {
        // http://localhost:11434/v1 -> http://localhost:11434
        if (Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri))
        {
            var builder = new UriBuilder(uri) { Path = "", Query = "", Fragment = "" };
            return builder.Uri.ToString().TrimEnd('/');
        }
        return "http://localhost:11434";
    }

    public async Task<(bool Ok, string Message)> PingAsync(string baseUrl, CancellationToken ct = default)
    {
        try
        {
            var host = HostFromBaseUrl(baseUrl);
            using var resp = await _http.GetAsync($"{host}/api/tags", ct);
            if (!resp.IsSuccessStatusCode)
                return (false, $"Ollama HTTP {(int)resp.StatusCode}");
            return (true, "Connected");
        }
        catch (Exception ex)
        {
            return (false, ex.Message);
        }
    }

    public async Task<IReadOnlyList<OllamaModelInfo>> ListModelsAsync(string baseUrl, CancellationToken ct = default)
    {
        var host = HostFromBaseUrl(baseUrl);
        using var resp = await _http.GetAsync($"{host}/api/tags", ct);
        resp.EnsureSuccessStatusCode();
        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
        var list = new List<OllamaModelInfo>();
        if (!doc.RootElement.TryGetProperty("models", out var models)) return list;
        foreach (var m in models.EnumerateArray())
        {
            var name = m.GetProperty("name").GetString() ?? "";
            var details = m.TryGetProperty("details", out var d) ? d : default;
            var family = details.ValueKind == JsonValueKind.Object && details.TryGetProperty("family", out var f)
                ? f.GetString() ?? "" : "";
            var param = details.ValueKind == JsonValueKind.Object && details.TryGetProperty("parameter_size", out var p)
                ? p.GetString() ?? "" : "";
            var size = m.TryGetProperty("size", out var s) ? s.GetInt64() : 0;
            var caps = m.TryGetProperty("capabilities", out var c) ? c : default;
            var isEmbed = false;
            if (caps.ValueKind == JsonValueKind.Array)
            {
                foreach (var cap in caps.EnumerateArray())
                    if (string.Equals(cap.GetString(), "embedding", StringComparison.OrdinalIgnoreCase))
                        isEmbed = true;
            }
            if (!isEmbed && name.Contains("embed", StringComparison.OrdinalIgnoreCase))
                isEmbed = true;

            list.Add(new OllamaModelInfo
            {
                Name = name,
                Family = family,
                ParameterSize = param,
                Size = size,
                IsEmbedding = isEmbed,
            });
        }
        return list.OrderBy(x => x.Name, StringComparer.OrdinalIgnoreCase).ToList();
    }

    public async Task<IReadOnlyList<string>> ListLoadedAsync(string baseUrl, CancellationToken ct = default)
    {
        var host = HostFromBaseUrl(baseUrl);
        using var resp = await _http.GetAsync($"{host}/api/ps", ct);
        resp.EnsureSuccessStatusCode();
        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
        var loaded = new List<string>();
        if (!doc.RootElement.TryGetProperty("models", out var models)) return loaded;
        foreach (var m in models.EnumerateArray())
        {
            if (m.TryGetProperty("name", out var n))
                loaded.Add(n.GetString() ?? "");
        }
        return loaded;
    }

    public async Task<(bool Ok, string Message)> WarmupAsync(
        string baseUrl,
        string model,
        object options,
        string keepAlive,
        CancellationToken ct = default)
    {
        try
        {
            // Hand-built JsonObject — no JsonSerializerOptions (single-file safe).
            var optsNode = new JsonObject();
            if (options is not null)
            {
                foreach (var prop in options.GetType().GetProperties())
                {
                    var val = prop.GetValue(options);
                    if (val is null) continue;
                    optsNode[prop.Name] = val switch
                    {
                        int i => i,
                        long l => l,
                        float f => f,
                        double d => d,
                        bool b => b,
                        string s => s,
                        _ => val.ToString(),
                    };
                }
            }
            var payload = new JsonObject
            {
                ["model"] = model,
                ["messages"] = new JsonArray
                {
                    new JsonObject { ["role"] = "user", ["content"] = "ping" },
                },
                ["temperature"] = 0.0,
                ["max_tokens"] = 1,
                ["keep_alive"] = keepAlive,
                ["options"] = optsNode,
            };
            var json = payload.ToJsonString();
            using var content = new StringContent(json, Encoding.UTF8, "application/json");
            using var resp = await _http.PostAsync($"{baseUrl.TrimEnd('/')}/chat/completions", content, ct);
            var body = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return (false, $"Warmup failed: HTTP {(int)resp.StatusCode} {body}");
            return (true, $"Warmed {model}");
        }
        catch (Exception ex)
        {
            return (false, ex.Message);
        }
    }

    public void Dispose() => _http.Dispose();
}

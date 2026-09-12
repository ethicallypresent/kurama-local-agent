using System.IO;

namespace KuramaStudio.Models;

/// <summary>One GGUF on disk for the Models page list.</summary>
public sealed class GgufFile
{
    public string FullPath { get; }
    public string Name { get; }
    public string Folder { get; }
    public string SizeLabel { get; }

    public GgufFile(string fullPath)
    {
        FullPath = Path.GetFullPath(fullPath);
        Name = Path.GetFileName(FullPath);
        Folder = Path.GetDirectoryName(FullPath) ?? "";
        SizeLabel = FormatSize(FullPath);
    }

    public override string ToString() => Name;

    public override bool Equals(object? obj) =>
        obj is GgufFile other &&
        string.Equals(FullPath, other.FullPath, StringComparison.OrdinalIgnoreCase);

    public override int GetHashCode() =>
        StringComparer.OrdinalIgnoreCase.GetHashCode(FullPath);

    private static string FormatSize(string path)
    {
        try
        {
            var bytes = new FileInfo(path).Length;
            if (bytes < 1024) return $"{bytes} B";
            if (bytes < 1024 * 1024) return $"{bytes / 1024.0:0} KB";
            if (bytes < 1024L * 1024 * 1024) return $"{bytes / (1024.0 * 1024):0.0} MB";
            return $"{bytes / (1024.0 * 1024 * 1024):0.00} GB";
        }
        catch
        {
            return "";
        }
    }
}

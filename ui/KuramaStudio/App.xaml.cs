using System.IO;
using System.Windows;
using System.Windows.Threading;
using KuramaStudio.Services;

namespace KuramaStudio;

public partial class App : Application
{
    public static LlamaServerService LlamaServer { get; } = new();
    private static readonly string CrashLogPath = Path.Combine(AppContext.BaseDirectory, "kurama_studio.log");

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        Log("startup");
        DispatcherUnhandledException += OnDispatcherUnhandledException;
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
            Log("unhandled: " + args.ExceptionObject);
        TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            Log("unobserved task: " + args.Exception);
            args.SetObserved();
        };
    }

    private static void OnDispatcherUnhandledException(object sender, DispatcherUnhandledExceptionEventArgs args)
    {
        Log("dispatcher: " + args.Exception);
        MessageBox.Show(args.Exception.ToString(), "Kurama Studio", MessageBoxButton.OK, MessageBoxImage.Error);
        args.Handled = true;
    }

    public static void Log(string message)
    {
        try
        {
            File.AppendAllText(CrashLogPath, $"[{DateTime.Now:HH:mm:ss}] {message}{Environment.NewLine}");
        }
        catch { /* ignore */ }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        Log("exit");
        try { LlamaServer.Dispose(); } catch { /* ignore */ }
        base.OnExit(e);
    }
}

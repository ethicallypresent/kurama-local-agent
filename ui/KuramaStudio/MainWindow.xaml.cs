using System.Collections.Specialized;
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using KuramaStudio.ViewModels;

namespace KuramaStudio;

public partial class MainWindow : Window
{
    private bool _stickToBottom = true;
    private bool _programmaticScroll;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel vm)
        {
            vm.Bubbles.CollectionChanged += Bubbles_CollectionChanged;
            vm.PropertyChanged += Vm_PropertyChanged;
            UpdateSendEnabled(vm);
        }
        ScrollChatToEnd(force: true);
    }

    private void Vm_PropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (sender is not MainViewModel vm) return;
        if (e.PropertyName is nameof(MainViewModel.GoalText) or nameof(MainViewModel.IsRunning))
            UpdateSendEnabled(vm);
        if (e.PropertyName is nameof(MainViewModel.IsRunning) && vm.IsRunning)
        {
            _stickToBottom = true;
            ScrollChatToEnd(force: true);
        }
    }

    private void UpdateSendEnabled(MainViewModel vm)
    {
        var hasText = !string.IsNullOrWhiteSpace(vm.GoalText);
        SendBtn.IsEnabled = hasText && !vm.IsRunning;
    }

    private void Bubbles_CollectionChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        if (e.NewItems is not null)
        {
            foreach (var item in e.NewItems)
            {
                if (item is INotifyPropertyChanged npc)
                {
                    npc.PropertyChanged -= Bubble_PropertyChanged;
                    npc.PropertyChanged += Bubble_PropertyChanged;
                }
            }
        }
        if (_stickToBottom) ScrollChatToEnd();
    }

    private void Bubble_PropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(ChatBubble.Text) or nameof(ChatBubble.Thinking) or nameof(ChatBubble.IsThinking))
        {
            if (_stickToBottom) ScrollChatToEnd();
        }
    }

    private void ChatScroll_ScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        if (_programmaticScroll) return;
        // Intent-aware autoscroll (Studio-style): stick only when near bottom;
        // if the user scrolls up, pause follow until they return / click ↓.
        const double threshold = 48;
        var atBottom = ChatScroll.ScrollableHeight <= 0
                       || ChatScroll.VerticalOffset >= ChatScroll.ScrollableHeight - threshold;
        _stickToBottom = atBottom;
        ScrollToBottomBtn.Visibility = _stickToBottom ? Visibility.Collapsed : Visibility.Visible;
    }

    private void ScrollToBottom_Click(object sender, RoutedEventArgs e)
    {
        _stickToBottom = true;
        ScrollChatToEnd(force: true);
        ScrollToBottomBtn.Visibility = Visibility.Collapsed;
    }

    private void ScrollChatToEnd(bool force = false)
    {
        if (!force && !_stickToBottom) return;
        _programmaticScroll = true;
        try
        {
            ChatScroll.Dispatcher.InvokeAsync(() =>
            {
                ChatScroll.UpdateLayout();
                ChatScroll.ScrollToEnd();
                _programmaticScroll = false;
            }, System.Windows.Threading.DispatcherPriority.Background);
        }
        catch
        {
            _programmaticScroll = false;
        }
    }

    private void Composer_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        // Enter sends; Shift+Enter inserts newline (common chat composers).
        if (e.Key == Key.Enter && (Keyboard.Modifiers & ModifierKeys.Shift) == 0)
        {
            e.Handled = true;
            if (SendBtn.IsEnabled && DataContext is MainViewModel vm && vm.RunAgentCommand.CanExecute(null))
                vm.RunAgentCommand.Execute(null);
        }
    }

    private void Send_Click(object sender, RoutedEventArgs e)
    {
        _stickToBottom = true;
        ScrollChatToEnd(force: true);
    }
}

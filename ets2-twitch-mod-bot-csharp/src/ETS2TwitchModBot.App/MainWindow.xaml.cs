using System;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using Microsoft.Win32;
using ETS2TwitchModBot.Core;

namespace ETS2TwitchModBot.App
{
    /// <summary>
    /// Code-behind for MainWindow.xaml
    /// Wires UI controls to the core services and provides a minimal runtime loop:
    /// - Start/Stop the bot and Twitch service
    /// - Periodically refresh Mods and DLC lists
    /// - Provide settings save/load and simple log panel
    /// </summary>
    public partial class MainWindow : Window
    {
        // Observable collections bound to UI
        private readonly ObservableCollection<ModInfo> _mods = new();
        private readonly ObservableCollection<string> _dlcs = new();

        // Core runtime objects (created when Start is pressed)
        private BotConfig? _config;
        private ModCache? _cache;
        private WorkshopLookup? _workshopLookup;
        private ModParser? _parser;
        private DLCDetector? _dlcDetector;
        private ETS2ModBot? _botCore;
        private TwitchBotService? _twitchService;

        // Background refresh loop cancellation
        private CancellationTokenSource? _cts;

        // Default cache file path relative to app data
        private readonly string _defaultCachePath;

        public MainWindow()
        {
            InitializeComponent();

            // Data binding of lists
            ModsItemsControl.ItemsSource = _mods;
            DlcsListBox.ItemsSource = _dlcs;

            // Wire up button handlers (XAML may or may not have Click handlers set directly)
            StartButton.Click += StartButton_Click;
            StopButton.Click += StopButton_Click;
            SaveSettingsButton.Click += SaveSettingsButton_Click;
            LoadFromEnvButton.Click += LoadFromEnvButton_Click;
            RescanModsButton.Click += RescanModsButton_Click;
            ClearLogsButton.Click += ClearLogsButton_Click;
            SaveLogsButton.Click += SaveLogsButton_Click;
            RefreshModsButton.Click += RescanModsButton_Click;
            OpenModsFolderButton.Click += OpenModsFolderButton_Click;
            OpenProfilesFolderButton.Click += OpenProfilesFolderButton_Click;
            ClearCacheButton.Click += ClearCacheButton_Click;

            // Default cache location (in same folder as app for simplicity)
            _defaultCachePath = Path.Combine(AppContext.BaseDirectory, "modcache.json");

            // Load settings into UI if appsettings exists
            LoadSettingsToUi();
            AppendLog("Ready.");
        }

        #region UI Event Handlers

        private async void StartButton_Click(object? sender, RoutedEventArgs e)
        {
            try
            {
                StartButton.IsEnabled = false;
                AppendLog("Starting bot...");

                // Build config from UI
                _config = BuildConfigFromUi();

                // Prepare services
                var cachePath = string.IsNullOrWhiteSpace(_config.Ets2ModPath)
                    ? _defaultCachePath
                    : Path.Combine(_config.Ets2ModPath, "modcache.json");

                _cache = new ModCache(cachePath);
                await _cache.LoadAsync().ConfigureAwait(false);

                _workshopLookup = new WorkshopLookup();
                _parser = new ModParser(_config, _cache, _workshopLookup);
                _dlcDetector = new DLCDetector(_config);
                _botCore = new ETS2ModBot(_config);

                // Twitch service (wiring)
                _twitchService = new TwitchBotService(_config, logger: null);
                _twitchService.OnMessageReceived += Twitch_OnMessageReceived;
                _twitchService.OnJoinedChannel += Twitch_OnJoinedChannel;

                await _twitchService.StartAsync().ConfigureAwait(false);

                // Kick off background refresh loop
                _cts = new CancellationTokenSource();
                _ = Task.Run(() => RefreshLoopAsync(_cts.Token));

                StatusTextBlock.Text = "Running";
                StopButton.IsEnabled = true;
                AppendLog("Bot started.");
            }
            catch (Exception ex)
            {
                AppendLog($"Error starting bot: {ex.Message}");
                StartButton.IsEnabled = true;
                StatusTextBlock.Text = "Error";
            }
        }

        private async void StopButton_Click(object? sender, RoutedEventArgs e)
        {
            try
            {
                AppendLog("Stopping bot...");
                StopButton.IsEnabled = false;
                StatusTextBlock.Text = "Stopping";

                // Cancel refresh loop
                _cts?.Cancel();

                if (_twitchService != null)
                {
                    await _twitchService.StopAsync().ConfigureAwait(false);
                    _twitchService.Dispose();
                    _twitchService = null;
                }

                _parser = null;
                _dlcDetector = null;
                _botCore = null;
                _workshopLookup?.Dispose();
                _workshopLookup = null;

                StartButton.IsEnabled = true;
                StatusTextBlock.Text = "Stopped";
                AppendLog("Bot stopped.");
            }
            catch (Exception ex)
            {
                AppendLog($"Error stopping bot: {ex.Message}");
                StatusTextBlock.Text = "Error";
                StartButton.IsEnabled = true;
            }
        }

        private void SaveSettingsButton_Click(object? sender, RoutedEventArgs e)
        {
            try
            {
                SaveSettingsFromUi();
                AppendLog("Settings saved to appsettings.json.");
            }
            catch (Exception ex)
            {
                AppendLog($"Failed to save settings: {ex.Message}");
            }
        }

        private void LoadFromEnvButton_Click(object? sender, RoutedEventArgs e)
        {
            try
            {
                LoadSettingsFromEnvironment();
                AppendLog("Settings loaded from environment variables (where available).");
            }
            catch (Exception ex)
            {
                AppendLog($"Failed to load from environment: {ex.Message}");
            }
        }

        private async void RescanModsButton_Click(object? sender, RoutedEventArgs e)
        {
            await RefreshOnceAsync().ConfigureAwait(false);
        }

        private void ClearLogsButton_Click(object? sender, RoutedEventArgs e)
        {
            LogsTextBox.Clear();
        }

        private void SaveLogsButton_Click(object? sender, RoutedEventArgs e)
        {
            var dlg = new SaveFileDialog
            {
                Title = "Save Logs",
                Filter = "Text files (*.txt)|*.txt|All files|*.*",
                FileName = $"ets2-twitch-mod-bot-logs-{DateTime.Now:yyyyMMdd-HHmmss}.txt"
            };
            if (dlg.ShowDialog(this) == true)
            {
                try
                {
                    File.WriteAllText(dlg.FileName, LogsTextBox.Text);
                    AppendLog($"Saved logs to {dlg.FileName}");
                }
                catch (Exception ex)
                {
                    AppendLog($"Failed to save logs: {ex.Message}");
                }
            }
        }

        private void OpenModsFolderButton_Click(object? sender, RoutedEventArgs e)
        {
            var path = Ets2ModPathBox.Text;
            if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
            {
                AppendLog("Mods folder path is empty or does not exist.");
                return;
            }
            Process.Start(new ProcessStartInfo("explorer", $"\"{path}\"") { UseShellExecute = true });
        }

        private void OpenProfilesFolderButton_Click(object? sender, RoutedEventArgs e)
        {
            var path = Ets2ProfilesPathBox.Text;
            if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
            {
                AppendLog("Profiles folder path is empty or does not exist.");
                return;
            }
            Process.Start(new ProcessStartInfo("explorer", $"\"{path}\"") { UseShellExecute = true });
        }

        private void ClearCacheButton_Click(object? sender, RoutedEventArgs e)
        {
            try
            {
                var cachePath = Path.Combine(Ets2ModPathBox.Text ?? "", "modcache.json");
                if (File.Exists(cachePath))
                {
                    File.Delete(cachePath);
                    AppendLog("Cleared mod cache file.");
                }
                else
                {
                    AppendLog("No cache file found to clear.");
                }
            }
            catch (Exception ex)
            {
                AppendLog($"Failed to clear cache: {ex.Message}");
            }
        }

        #endregion

        #region Background Refresh Loop

        private async Task RefreshLoopAsync(CancellationToken cancellationToken)
        {
            AppendLog("Entering refresh loop.");
            try
            {
                while (!cancellationToken.IsCancellationRequested)
                {
                    try
                    {
                        await RefreshOnceAsync().ConfigureAwait(false);
                    }
                    catch (Exception ex)
                    {
                        AppendLog($"Refresh error: {ex.Message}");
                    }

                    // Wait ~30 seconds between refreshes, but can be tuned
                    await Task.Delay(TimeSpan.FromSeconds(30), cancellationToken).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException)
            {
                // expected on stop
            }
            catch (Exception ex)
            {
                AppendLog($"Refresh loop terminated unexpectedly: {ex.Message}");
            }
            finally
            {
                AppendLog("Exiting refresh loop.");
            }
        }

        private async Task RefreshOnceAsync()
        {
            // Ensure parser exists
            if (_parser == null)
            {
                AppendLog("Parser not initialized; cannot refresh.");
                return;
            }

            try
            {
                // Parse mods from folder
                var mods = await _parser.ParseFromFolderAsync().ConfigureAwait(false);

                // Update UI with mod list
                await Dispatcher.InvokeAsync(() =>
                {
                    _mods.Clear();
                    foreach (var m in mods)
                    {
                        _mods.Add(m);
                    }
                }, DispatcherPriority.Background);

                AppendLog($"Parsed {mods.Count} mods.");

                // Detect DLCs
                if (_dlcDetector != null)
                {
                    var dlcs = await _dlcDetector.GetActiveDlcAsync().ConfigureAwait(false);
                    await Dispatcher.InvokeAsync(() =>
                    {
                        _dlcs.Clear();
                        foreach (var d in dlcs)
                        {
                            _dlcs.Add(d);
                        }
                    }, DispatcherPriority.Background);

                    AppendLog($"Detected {dlcs.Count} DLC(s).");
                }

                // Persist cache (best-effort)
                if (_cache != null)
                {
                    await _cache.SaveAsync().ConfigureAwait(false);
                }
            }
            catch (Exception ex)
            {
                AppendLog($"Error during refresh: {ex.Message}");
            }
        }

        #endregion

        #region Twitch event handlers

        private void Twitch_OnJoinedChannel(object? sender, OnJoinedChannelArgs e)
        {
            AppendLog($"Joined channel: {e.Channel}");
        }

        private void Twitch_OnMessageReceived(object? sender, OnMessageReceivedArgs e)
        {
            // Basic command dispatch example: !mods -> replies with current mod list count
            try
            {
                var message = e.ChatMessage?.Message ?? string.Empty;
                var user = e.ChatMessage?.DisplayName ?? e.ChatMessage?.Username ?? "unknown";
                AppendLog($"<{user}> {message}");

                if (string.IsNullOrWhiteSpace(message) || _twitchService == null) return;

                if (message.StartsWith("!mods", StringComparison.OrdinalIgnoreCase))
                {
                    // Create a short response summarizing mods (first few)
                    var toSend = "No mods detected.";
                    if (_mods.Count > 0)
                    {
                        var preview = string.Join(", ", _mods.Count > 5 ? _mods[..5] : _mods);
                        toSend = $"Mods ({_mods.Count}): {preview}";
                    }

                    // Fire-and-forget sending - the service will handle send exceptions
                    _ = _twitchService.SendMessageAsync(toSend);
                }
                else if (message.StartsWith("!dlc", StringComparison.OrdinalIgnoreCase))
                {
                    var toSend = _dlcs.Count == 0 ? "No DLC detected." : $"DLC: {string.Join(\", \", _dlcs)}";
                    _ = _twitchService.SendMessageAsync(toSend);
                }
                // Additional commands can be added here and wired to the core bot logic.
            }
            catch (Exception ex)
            {
                AppendLog($"Error handling chat message: {ex.Message}");
            }
        }

        #endregion

        #region Settings persistence

        private BotConfig BuildConfigFromUi()
        {
            var cfg = new BotConfig
            {
                TwitchToken = TwitchTokenBox.Password,
                TwitchChannel = TwitchChannelBox.Text?.Trim() ?? string.Empty,
                Ets2ModPath = Ets2ModPathBox.Text?.Trim() ?? string.Empty,
                Ets2ProfilePath = Ets2ProfilesPathBox.Text?.Trim() ?? string.Empty,
                Ets2SteamPath = Ets2SteamPathBox.Text?.Trim() ?? string.Empty,
                SteamApiKey = SteamApiKeyBox.Text?.Trim(),
                UserCooldownSeconds = 10,
                GlobalCooldownSeconds = 2
            };
            return cfg;
        }

        private void LoadSettingsToUi()
        {
            try
            {
                var path = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
                if (!File.Exists(path)) return;

                var doc = JsonDocument.Parse(File.ReadAllText(path));
                if (doc.RootElement.TryGetProperty("Twitch", out var twitch))
                {
                    if (twitch.TryGetProperty("OAuthToken", out var token)) TwitchTokenBox.Password = token.GetString() ?? "";
                    if (twitch.TryGetProperty("Channel", out var channel)) TwitchChannelBox.Text = channel.GetString() ?? "";
                }

                if (doc.RootElement.TryGetProperty("Paths", out var paths))
                {
                    if (paths.TryGetProperty("Ets2ModPath", out var mp)) Ets2ModPathBox.Text = mp.GetString() ?? "";
                    if (paths.TryGetProperty("Ets2ProfilePath", out var pp)) Ets2ProfilesPathBox.Text = pp.GetString() ?? "";
                    if (paths.TryGetProperty("Ets2SteamPath", out var sp)) Ets2SteamPathBox.Text = sp.GetString() ?? "";
                }

                if (doc.RootElement.TryGetProperty("Steam", out var steam))
                {
                    if (steam.TryGetProperty("ApiKey", out var key)) SteamApiKeyBox.Text = key.GetString() ?? "";
                }

                AppendLog("Loaded settings from appsettings.json");
            }
            catch (Exception ex)
            {
                AppendLog($"Failed to load settings: {ex.Message}");
            }
        }

        private void SaveSettingsFromUi()
        {
            var cfg = new
            {
                Twitch = new
                {
                    OAuthToken = TwitchTokenBox.Password,
                    Channel = TwitchChannelBox.Text?.Trim() ?? ""
                },
                Paths = new
                {
                    Ets2ModPath = Ets2ModPathBox.Text?.Trim() ?? "",
                    Ets2ProfilePath = Ets2ProfilesPathBox.Text?.Trim() ?? "",
                    Ets2SteamPath = Ets2SteamPathBox.Text?.Trim() ?? ""
                },
                Steam = new
                {
                    ApiKey = SteamApiKeyBox.Text?.Trim() ?? ""
                }
            };

            var json = JsonSerializer.Serialize(cfg, new JsonSerializerOptions { WriteIndented = true });
            var path = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
            File.WriteAllText(path, json);
        }

        private void LoadSettingsFromEnvironment()
        {
            var token = Environment.GetEnvironmentVariable("ETS2_TWITCH_OAUTH") ?? Environment.GetEnvironmentVariable("TWITCH_OAUTH_TOKEN");
            if (!string.IsNullOrWhiteSpace(token)) TwitchTokenBox.Password = token;

            var channel = Environment.GetEnvironmentVariable("ETS2_TWITCH_CHANNEL") ?? Environment.GetEnvironmentVariable("TWITCH_CHANNEL");
            if (!string.IsNullOrWhiteSpace(channel)) TwitchChannelBox.Text = channel;

            var modPath = Environment.GetEnvironmentVariable("ETS2_MOD_PATH");
            if (!string.IsNullOrWhiteSpace(modPath)) Ets2ModPathBox.Text = modPath;

            var profilePath = Environment.GetEnvironmentVariable("ETS2_PROFILE_PATH");
            if (!string.IsNullOrWhiteSpace(profilePath)) Ets2ProfilesPathBox.Text = profilePath;

            var steamPath = Environment.GetEnvironmentVariable("ETS2_STEAM_PATH");
            if (!string.IsNullOrWhiteSpace(steamPath)) Ets2SteamPathBox.Text = steamPath;

            var steamKey = Environment.GetEnvironmentVariable("STEAM_API_KEY");
            if (!string.IsNullOrWhiteSpace(steamKey)) SteamApiKeyBox.Text = steamKey;
        }

        #endregion

        #region Helpers

        private void AppendLog(string message)
        {
            var line = $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}{Environment.NewLine}";
            // Ensure UI update on UI thread
            Dispatcher.BeginInvoke(() =>
            {
                LogsTextBox.AppendText(line);
                LogsTextBox.ScrollToEnd();
            }, DispatcherPriority.Background);
        }

        #endregion
    }
}

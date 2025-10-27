using System;
using System.Collections.Concurrent;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using TwitchLib.Client;
using TwitchLib.Client.Events;
using TwitchLib.Client.Models;

namespace ETS2TwitchModBot.Core
{
    /// <summary>
    /// Helper service that resolves Steam Workshop item IDs (publishedfileid) to a human-readable display name
    /// using the Steam Web API. Results are cached in-memory for the lifetime of the process.
    ///
    /// Usage:
    ///   var lookup = new WorkshopLookup(httpClient, logger);
    ///   var name = await lookup.GetWorkshopItemNameAsync("1234567890", steamApiKey);
    /// </summary>
    public class WorkshopLookup : IDisposable
    {
        private readonly HttpClient _http;
        private readonly ILogger<WorkshopLookup>? _logger;
        private readonly ConcurrentDictionary<string, string?> _cache = new();
        private bool _disposed;

        // Steam endpoint to fetch published file details
        private const string SteamGetPublishedFileDetailsUrl = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/";

        public WorkshopLookup(HttpClient? httpClient = null, ILogger<WorkshopLookup>? logger = null)
        {
            _http = httpClient ?? new HttpClient();
            _logger = logger;
        }

        /// <summary>
        /// Resolve a workshop id (publishedfileid) into a display name.
        /// Returns null if the item could not be resolved.
        /// </summary>
        public async Task<string?> GetWorkshopItemNameAsync(string publishedFileId, string? steamApiKey = null, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(publishedFileId)) return null;

            // Check in-memory cache first
            if (_cache.TryGetValue(publishedFileId, out var cached) && cached is not null)
            {
                return cached;
            }

            try
            {
                // Steam's GetPublishedFileDetails uses POST with form-encoded body.
                // The API does not strictly require an API key for this request in many cases,
                // but having a Steam Web API key may be necessary for rate limits or additional metadata.
                using var request = new HttpRequestMessage(HttpMethod.Post, SteamGetPublishedFileDetailsUrl);
                var form = new MultipartFormDataContent();

                // itemcount and publishedfileids[0]
                form.Add(new StringContent("1"), "itemcount");
                form.Add(new StringContent(publishedFileId), "publishedfileids[0]");

                // Some endpoints accept an API key as 'key' query/form parameter. Add if provided.
                if (!string.IsNullOrWhiteSpace(steamApiKey))
                {
                    form.Add(new StringContent(steamApiKey), "key");
                }

                request.Content = form;

                using var resp = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
                if (!resp.IsSuccessStatusCode)
                {
                    _logger?.LogWarning("WorkshopLookup: Steam API returned non-success status {Status} for id {Id}", resp.StatusCode, publishedFileId);
                    _cache.TryAdd(publishedFileId, null);
                    return null;
                }

                var json = await resp.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
                if (string.IsNullOrWhiteSpace(json))
                {
                    _cache.TryAdd(publishedFileId, null);
                    return null;
                }

                // Parse the JSON response; expected structure contains 'response' -> 'publishedfiledetails' array
                try
                {
                    using var doc = JsonDocument.Parse(json);
                    if (doc.RootElement.TryGetProperty("response", out var responseElem) &&
                        responseElem.TryGetProperty("publishedfiledetails", out var detailsElem) &&
                        detailsElem.ValueKind == JsonValueKind.Array &&
                        detailsElem.GetArrayLength() > 0)
                    {
                        var first = detailsElem[0];
                        if (first.TryGetProperty("result", out var resultElem) && resultElem.GetInt32() != 1)
                        {
                            // non-success result
                            _logger?.LogDebug("WorkshopLookup: Steam API result for {Id} is not successful: {Result}", publishedFileId, resultElem.GetInt32());
                            _cache.TryAdd(publishedFileId, null);
                            return null;
                        }

                        if (first.TryGetProperty("title", out var titleElem))
                        {
                            var title = titleElem.GetString();
                            if (!string.IsNullOrWhiteSpace(title))
                            {
                                _cache.TryAdd(publishedFileId, title);
                                return title;
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    _logger?.LogError(ex, "WorkshopLookup: Failed to parse Steam API JSON for id {Id}", publishedFileId);
                }

                _cache.TryAdd(publishedFileId, null);
                return null;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "WorkshopLookup: Exception while resolving workshop id {Id}", publishedFileId);
                _cache.TryAdd(publishedFileId, null);
                return null;
            }
        }

        /// <summary>
        /// Utility that attempts to resolve multiple IDs with caching.
        /// </summary>
        public async Task<ConcurrentDictionary<string, string?>> GetWorkshopNamesBulkAsync(string[] publishedFileIds, string? steamApiKey = null, CancellationToken cancellationToken = default)
        {
            var results = new ConcurrentDictionary<string, string?>();
            var tasks = new Task[publishedFileIds.Length];

            for (int i = 0; i < publishedFileIds.Length; i++)
            {
                var id = publishedFileIds[i];
                tasks[i] = Task.Run(async () =>
                {
                    var name = await GetWorkshopItemNameAsync(id, steamApiKey, cancellationToken).ConfigureAwait(false);
                    results[id] = name;
                }, cancellationToken);
            }

            await Task.WhenAll(tasks).ConfigureAwait(false);
            return results;
        }

        public void Dispose()
        {
            if (_disposed) return;
            _http?.Dispose();
            _disposed = true;
        }
    }

    // ----------------------------------------------------------------------
    // Twitch wiring stubs (TwitchLib integration)
    // ----------------------------------------------------------------------
    //
    // These are lightweight wiring stubs to be completed in the App project. They
    // demonstrate how to hook into TwitchLib.Client events and route commands to
    // the core library. The App should provide a fully configured TwitchLib client
    // (with credentials from BotConfig) and set up DI + logging.
    //
    // The stubs below intentionally avoid bringing heavy runtime logic into the
    // core library; they are small helpers that the WPF app can instantiate.

    /// <summary>
    /// Thin wrapper that hosts a TwitchLib client and exposes lifecycle operations.
    /// The actual credentials and message processing should be provided by the hosting app.
    /// </summary>
    public sealed class TwitchBotService : IDisposable
    {
        private readonly BotConfig _config;
        private readonly ILogger<TwitchBotService>? _logger;
        private readonly TwitchClient _client;
        private bool _connected;

        public event EventHandler<OnMessageReceivedArgs>? OnMessageReceived;
        public event EventHandler<OnJoinedChannelArgs>? OnJoinedChannel;

        public TwitchBotService(BotConfig config, ILogger<TwitchBotService>? logger = null)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _logger = logger;

            // Note: TwitchLib.Client requires a ConnectionCredentials object.
            var credentials = new ConnectionCredentials("unused", _config.TwitchToken ?? string.Empty);
            _client = new TwitchClient();
            _client.Initialize(credentials, _config.TwitchChannel ?? string.Empty);

            // Hook basic events
            _client.OnMessageReceived += Client_OnMessageReceived;
            _client.OnJoinedChannel += Client_OnJoinedChannel;
            _client.OnConnectionError += Client_OnConnectionError;
            _client.OnDisconnected += Client_OnDisconnected;
        }

        /// <summary>
        /// Start the Twitch client. This opens the connection to the chat.
        /// </summary>
        public Task StartAsync()
        {
            if (_connected)
            {
                _logger?.LogDebug("TwitchBotService already started");
                return Task.CompletedTask;
            }

            try
            {
                _client.Connect();
                _connected = true;
                _logger?.LogInformation("TwitchBotService started and connecting to channel {Channel}", _config.TwitchChannel);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "TwitchBotService failed to connect");
                throw;
            }

            return Task.CompletedTask;
        }

        /// <summary>
        /// Stop the Twitch client.
        /// </summary>
        public Task StopAsync()
        {
            if (!_connected) return Task.CompletedTask;
            try
            {
                _client.Disconnect();
                _connected = false;
                _logger?.LogInformation("TwitchBotService disconnected");
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "TwitchBotService failed to disconnect cleanly");
            }
            return Task.CompletedTask;
        }

        /// <summary>
        /// Send a message to the channel (best-effort).
        /// </summary>
        public Task SendMessageAsync(string message)
        {
            if (!_connected)
            {
                _logger?.LogWarning("TwitchBotService: cannot send message while disconnected");
                return Task.CompletedTask;
            }

            try
            {
                _client.SendMessage(_config.TwitchChannel ?? string.Empty, message);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Failed to send message to channel");
            }

            return Task.CompletedTask;
        }

        private void Client_OnMessageReceived(object? sender, OnMessageReceivedArgs e)
        {
            try
            {
                // Bubble out to consumers for handling and command dispatching.
                OnMessageReceived?.Invoke(this, e);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Exception in OnMessageReceived handler");
            }
        }

        private void Client_OnJoinedChannel(object? sender, OnJoinedChannelArgs e)
        {
            try
            {
                OnJoinedChannel?.Invoke(this, e);
            }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "Exception in OnJoinedChannel handler");
            }
        }

        private void Client_OnConnectionError(object? sender, OnConnectionErrorArgs e)
        {
            _logger?.LogError("Twitch connection error: {Msg}", e.Error.Message);
        }

        private void Client_OnDisconnected(object? sender, OnDisconnectedEventArgs e)
        {
            _logger?.LogInformation("Twitch client disconnected");
        }

        public void Dispose()
        {
            try
            {
                _client.OnMessageReceived -= Client_OnMessageReceived;
                _client.OnJoinedChannel -= Client_OnJoinedChannel;
                _client.OnConnectionError -= Client_OnConnectionError;
                _client.OnDisconnected -= Client_OnDisconnected;
                if (_client.IsConnected) _client.Disconnect();
            }
            catch { }
        }
    }
}

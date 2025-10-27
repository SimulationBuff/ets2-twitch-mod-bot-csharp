/Users/richardadams/sourcecode/ets2-twitch-mod-bot/ets2-twitch-mod-bot-csharp/src/ETS2TwitchModBot.Core/src/WorkshopLookup.cs#L1-999
using System;
using System.Collections.Concurrent;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;

namespace ETS2TwitchModBot.Core
{
    /// <summary>
    /// Resolves Steam Workshop published file IDs (publishedfileid) to human readable titles.
    /// - In-memory caching for the lifetime of the process
    /// - Concurrency throttling to reduce parallel calls to Steam
    /// - Retries with exponential backoff for transient errors
    /// - Honors 429 Retry-After header
    /// </summary>
    public sealed class WorkshopLookup : IDisposable
    {
        private readonly HttpClient _http;
        private readonly ILogger<WorkshopLookup>? _logger;
        private readonly ConcurrentDictionary<string, string?> _cache = new();
        private readonly SemaphoreSlim _throttle;
        private readonly int _maxRetries;
        private readonly TimeSpan _baseDelay;
        private readonly double _jitterFactor;
        private bool _disposed;

        private const string SteamGetPublishedFileDetailsUrl = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/";

        /// <summary>
        /// Construct a new instance.
        /// </summary>
        /// <param name="httpClient">Optional HttpClient to use. If null a new instance will be created.</param>
        /// <param name="logger">Optional logger.</param>
        /// <param name="maxConcurrency">Maximum concurrent requests to Steam.</param>
        /// <param name="maxRetries">Maximum retries for transient failures (>=1).</param>
        /// <param name="baseDelay">Base delay used for exponential backoff (first retry will wait ~baseDelay).</param>
        /// <param name="jitterFactor">Relative jitter applied to backoff delays (0.0 = no jitter, 1.0 = full jitter).</param>
        public WorkshopLookup(HttpClient? httpClient = null, ILogger<WorkshopLookup>? logger = null,
                              int maxConcurrency = 4, int maxRetries = 3,
                              TimeSpan? baseDelay = null, double jitterFactor = 0.2)
        {
            _http = httpClient ?? new HttpClient();
            _logger = logger;
            _throttle = new SemaphoreSlim(Math.Max(1, maxConcurrency));
            _maxRetries = Math.Max(1, maxRetries);
            _baseDelay = baseDelay ?? TimeSpan.FromSeconds(1);
            _jitterFactor = Math.Max(0.0, Math.Min(1.0, jitterFactor));
        }

        /// <summary>
        /// Resolve a single workshop item id to a display name. Returns null if resolution failed.
        /// This method is resilient to transient HTTP errors and will retry with backoff.
        /// </summary>
        public async Task<string?> GetWorkshopItemNameAsync(string publishedFileId, string? steamApiKey = null, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(publishedFileId)) return null;

            // Fast-path: check cache
            if (_cache.TryGetValue(publishedFileId, out var cached) && cached is not null)
            {
                return cached;
            }

            await _throttle.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                // Re-check cache after acquiring throttle, in case another caller populated it.
                if (_cache.TryGetValue(publishedFileId, out var cached2) && cached2 is not null)
                {
                    return cached2;
                }

                int attempt = 0;
                while (true)
                {
                    attempt++;
                    try
                    {
                        using var request = new HttpRequestMessage(HttpMethod.Post, SteamGetPublishedFileDetailsUrl);

                        // Steam expects form data. MultipartFormDataContent is acceptable and used by other implementations.
                        var form = new MultipartFormDataContent
                        {
                            { new StringContent("1"), "itemcount" },
                            { new StringContent(publishedFileId), "publishedfileids[0]" }
                        };

                        if (!string.IsNullOrWhiteSpace(steamApiKey))
                        {
                            form.Add(new StringContent(steamApiKey), "key");
                        }

                        request.Content = form;

                        using var resp = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);

                        if (resp.IsSuccessStatusCode)
                        {
                            var json = await resp.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
                            if (string.IsNullOrWhiteSpace(json))
                            {
                                _cache.TryAdd(publishedFileId, null);
                                return null;
                            }

                            try
                            {
                                using var doc = JsonDocument.Parse(json);
                                if (doc.RootElement.TryGetProperty("response", out var responseElem) &&
                                    responseElem.TryGetProperty("publishedfiledetails", out var detailsElem) &&
                                    detailsElem.ValueKind == JsonValueKind.Array &&
                                    detailsElem.GetArrayLength() > 0)
                                {
                                    var first = detailsElem[0];

                                    // check result code
                                    if (first.TryGetProperty("result", out var resultElem) && resultElem.ValueKind == JsonValueKind.Number)
                                    {
                                        // Steam uses result != 1 to signal non-success
                                        if (resultElem.GetInt32() != 1)
                                        {
                                            _logger?.LogDebug("WorkshopLookup: Steam API returned non-success result {Result} for id {Id}", resultElem.GetInt32(), publishedFileId);
                                            _cache.TryAdd(publishedFileId, null);
                                            return null;
                                        }
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
                            catch (JsonException jex)
                            {
                                _logger?.LogError(jex, "WorkshopLookup: failed to parse Steam JSON for id {Id}", publishedFileId);
                            }

                            // If we couldn't extract a title, cache null to avoid repeated lookups.
                            _cache.TryAdd(publishedFileId, null);
                            return null;
                        }

                        // Handle 429 - Too Many Requests (rate limiting)
                        if ((int)resp.StatusCode == 429)
                        {
                            // If Steam provides Retry-After, honor it.
                            if (resp.Headers.TryGetValues("Retry-After", out var values))
                            {
                                var s = values.FirstOrDefault();
                                if (!string.IsNullOrWhiteSpace(s))
                                {
                                    // Retry-After can be seconds or HTTP-date.
                                    if (int.TryParse(s, out var seconds) && seconds > 0)
                                    {
                                        _logger?.LogWarning("WorkshopLookup: received 429 for {Id}; retrying after {Seconds}s", publishedFileId, seconds);
                                        await Task.Delay(TimeSpan.FromSeconds(seconds), cancellationToken).ConfigureAwait(false);
                                        continue;
                                    }
                                    else if (DateTimeOffset.TryParse(s, out var when))
                                    {
                                        var delay = when - DateTimeOffset.UtcNow;
                                        if (delay < TimeSpan.Zero) delay = TimeSpan.Zero;
                                        _logger?.LogWarning("WorkshopLookup: received 429 for {Id}; retrying after {Delay}", publishedFileId, delay);
                                        await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
                                        continue;
                                    }
                                }
                            }

                            // If no Retry-After header present, fall through to exponential backoff below.
                            _logger?.LogWarning("WorkshopLookup: received 429 for {Id}; applying backoff (attempt {Attempt})", publishedFileId, attempt);
                        }

                        // Server errors (5xx) are usually transient - retry up to max
                        if ((int)resp.StatusCode >= 500 && attempt < _maxRetries)
                        {
                            var delay = ComputeBackoff(attempt);
                            _logger?.LogWarning("WorkshopLookup: server error {Status} for {Id}; retrying in {Delay} (attempt {Attempt})", resp.StatusCode, publishedFileId, delay, attempt);
                            await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
                            continue;
                        }

                        // Non-retriable status - cache null and return
                        _logger?.LogDebug("WorkshopLookup: Steam API returned non-success status {Status} for id {Id}", resp.StatusCode, publishedFileId);
                        _cache.TryAdd(publishedFileId, null);
                        return null;
                    }
                    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                    {
                        throw;
                    }
                    catch (Exception ex)
                    {
                        _logger?.LogWarning(ex, "WorkshopLookup: exception resolving {Id} (attempt {Attempt})", publishedFileId, attempt);
                        if (attempt >= _maxRetries)
                        {
                            _logger?.LogError(ex, "WorkshopLookup: failing after {Attempts} attempts for id {Id}", attempt, publishedFileId);
                            _cache.TryAdd(publishedFileId, null);
                            return null;
                        }

                        var delay = ComputeBackoff(attempt);
                        await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
                        continue;
                    }
                }
            }
            finally
            {
                _throttle.Release();
            }
        }

        /// <summary>
        /// Resolve multiple publishedfileids concurrently while respecting the configured throttle.
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

        private TimeSpan ComputeBackoff(int attempt)
        {
            // Exponential backoff with jitter: baseDelay * 2^(attempt-1) +/- jitter
            var exponential = TimeSpan.FromMilliseconds(_baseDelay.TotalMilliseconds * Math.Pow(2, Math.Max(0, attempt - 1)));
            if (_jitterFactor <= 0) return exponential;

            var jitterMs = exponential.TotalMilliseconds * _jitterFactor;
            var min = Math.Max(0, exponential.TotalMilliseconds - jitterMs);
            var max = exponential.TotalMilliseconds + jitterMs;
            var ms = Random.Shared.NextDouble() * (max - min) + min;
            return TimeSpan.FromMilliseconds(ms);
        }

        public void Dispose()
        {
            if (_disposed) return;
            try { _http?.Dispose(); } catch { }
            try { _throttle?.Dispose(); } catch { }
            _disposed = true;
        }
    }
}

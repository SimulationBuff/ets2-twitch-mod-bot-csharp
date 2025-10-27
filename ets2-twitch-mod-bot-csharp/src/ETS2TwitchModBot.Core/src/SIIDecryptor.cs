/*
  SIIDecryptor.cs

  Provides:
  - SII decryption constants and key (SII_KEY, signatures)
  - SIIDecryptor: reads SII files (plain or encrypted)
  - Lightweight stubs for several core types so the library compiles:
    BotConfig, ModCache, ModParser, CooldownManager, SingleInstanceLock,
    DLCDetector, ETS2ModBot.

  Notes:
  - This is a pragmatic, self-contained implementation intended as a starting
    point for the .NET port. The decryptor tries AES-256-CBC decryption and
    optionally zlib decompression (by inspecting the decrypted bytes).
  - The core classes are minimal and intended to be expanded with full logic
    and tests in follow-up commits.
*/

using System;
using System.Buffers;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace ETS2TwitchModBot.Core
{
    public static class SIIConstants
    {
        // Signatures for SII files
        public const uint SII_SIGNATURE_ENCRYPTED = 0x43736353; // "ScsC"
        public const uint SII_SIGNATURE_NORMAL = 0x4E696953;    // "SiiN"

        // SII AES key (32 bytes) — copied from community research in original project.
        public static readonly byte[] SII_KEY = new byte[]
        {
            0x2A, 0x5F, 0xCB, 0x17, 0x91, 0xD2, 0x2F, 0xB6,
            0x02, 0x45, 0xB3, 0xD8, 0x36, 0x9E, 0xD0, 0xB2,
            0xC2, 0x73, 0x71, 0x56, 0x3F, 0xBF, 0x1F, 0x3C,
            0x9E, 0xDF, 0x6B, 0x11, 0x82, 0x5A, 0x5D, 0x0A
        };
    }

    public static class SIIDecryptor
    {
        /// <summary>
        /// Read and decrypt an SII profile file. Returns the file text on success, or null on failure.
        /// Handles plain-text (SII_SIGNATURE_NORMAL) and encrypted (SII_SIGNATURE_ENCRYPTED) files.
        /// </summary>
        public static async Task<string?> DecryptFileAsync(string filePath)
        {
            if (string.IsNullOrWhiteSpace(filePath)) return null;
            if (!File.Exists(filePath)) return null;

            byte[] data;
            try
            {
                data = await File.ReadAllBytesAsync(filePath).ConfigureAwait(false);
            }
            catch
            {
                return null;
            }

            if (data.Length < 4)
                return null;

            uint signature = BitConverter.ToUInt32(data, 0);

            if (signature == SIIConstants.SII_SIGNATURE_NORMAL)
            {
                // Plaintext SII: remainder is UTF-8 text
                try
                {
                    return Encoding.UTF8.GetString(data, 4, data.Length - 4);
                }
                catch
                {
                    // fallback to best-effort decoding
                    return Encoding.UTF8.GetString(data, 4, data.Length - 4);
                }
            }

            if (signature == SIIConstants.SII_SIGNATURE_ENCRYPTED)
            {
                return await DecryptEncryptedAsync(data).ConfigureAwait(false);
            }

            // Unknown signature
            return null;
        }

        private static async Task<string?> DecryptEncryptedAsync(byte[] data)
        {
            // Expected header size: 4 (sig) + 32 (hmac placeholder) + 16 (iv) + 4 (datasize) = 56
            const int headerSize = 4 + 32 + 16 + 4;
            if (data.Length < headerSize) return null;

            try
            {
                // IV at bytes 36..51 (0-based)
                int ivOffset = 4 + 32;
                byte[] iv = new byte[16];
                Array.Copy(data, ivOffset, iv, 0, 16);

                int payloadOffset = headerSize;
                int payloadLength = data.Length - payloadOffset;
                if (payloadLength <= 0) return null;

                byte[] encryptedPayload = new byte[payloadLength];
                Array.Copy(data, payloadOffset, encryptedPayload, 0, payloadLength);

                // AES-256-CBC decrypt
                byte[] decrypted;
                using (Aes aes = Aes.Create())
                {
                    aes.KeySize = 256;
                    aes.Key = SIIConstants.SII_KEY;
                    aes.IV = iv;
                    aes.Mode = CipherMode.CBC;
                    // use PKCS7 padding: the encryptor in many implementations uses PKCS7
                    aes.Padding = PaddingMode.PKCS7;

                    using var decryptor = aes.CreateDecryptor();
                    try
                    {
                        decrypted = decryptor.TransformFinalBlock(encryptedPayload, 0, encryptedPayload.Length);
                    }
                    catch
                    {
                        // If PKCS7 fails or data not correct length try no padding and manual unpadding
                        aes.Padding = PaddingMode.None;
                        using var decryptorNoPad = aes.CreateDecryptor();
                        var raw = decryptorNoPad.TransformFinalBlock(encryptedPayload, 0, encryptedPayload.Length);
                        // try remove PKCS7 if present
                        decrypted = TryRemovePkcs7Padding(raw);
                    }
                }

                if (decrypted == null || decrypted.Length == 0) return null;

                // Attempt zlib decompression: zlib data often begins with 0x78
                try
                {
                    if (decrypted.Length >= 2 && (decrypted[0] == 0x78))
                    {
                        // zlib stream: skip 2-byte header and let DeflateStream decompress the remainder.
                        // Many zlib streams have a 2-byte header and a 4-byte Adler32 checksum at the end.
                        // We let DeflateStream read and ignore checksum bytes if present.
                        using var ms = new MemoryStream(decrypted, 2, decrypted.Length - 2);
                        using var ds = new DeflateStream(ms, CompressionMode.Decompress);
                        using var sr = new StreamReader(ds, Encoding.UTF8);
                        string decompressed = await sr.ReadToEndAsync().ConfigureAwait(false);
                        if (!string.IsNullOrEmpty(decompressed)) return decompressed;
                    }
                }
                catch
                {
                    // swallow and try fallback below
                }

                // Fallback: interpret decrypted bytes as UTF-8 text
                try
                {
                    return Encoding.UTF8.GetString(decrypted);
                }
                catch
                {
                    // last resort: try ASCII
                    return Encoding.ASCII.GetString(decrypted);
                }
            }
            catch
            {
                return null;
            }
        }

        private static byte[] TryRemovePkcs7Padding(byte[] data)
        {
            if (data == null || data.Length == 0) return data;
            int padLen = data[data.Length - 1];
            if (padLen <= 0 || padLen > 16 || padLen > data.Length) return data;
            // Verify padding bytes
            for (int i = data.Length - padLen; i < data.Length; i++)
            {
                // If padding not correct, return original
                if (data[i] != padLen) return data;
            }
            var result = new byte[data.Length - padLen];
            Array.Copy(data, 0, result, 0, result.Length);
            return result;
        }
    }

    // -------------------------
    // Core class stubs
    // -------------------------

    /// <summary>
    /// Minimal configuration holder for the bot.
    /// Expand this to map to appsettings.json and DI configuration.
    /// </summary>
    public sealed class BotConfig
    {
        public string TwitchToken { get; init; } = string.Empty;
        public string TwitchChannel { get; init; } = string.Empty;
        public string Ets2ModPath { get; init; } = string.Empty;
        public string Ets2ProfilePath { get; init; } = string.Empty;
        public string Ets2SteamPath { get; init; } = string.Empty;

        public int UserCooldownSeconds { get; init; } = 10;
        public int GlobalCooldownSeconds { get; init; } = 2;
    }

    /// <summary>
    /// Simple JSON-backed cache for mod filename -> display name.
    /// This is intentionally minimal; expand with locking and error handling as needed.
    /// </summary>
    public sealed class ModCache
    {
        private readonly string _path;
        private readonly ConcurrentDictionary<string, string> _map = new();

        public ModCache(string filePath)
        {
            _path = filePath ?? throw new ArgumentNullException(nameof(filePath));
        }

        public async Task LoadAsync()
        {
            try
            {
                if (!File.Exists(_path)) return;
                var text = await File.ReadAllTextAsync(_path).ConfigureAwait(false);
                var dict = System.Text.Json.JsonSerializer.Deserialize<Dictionary<string, string>>(text);
                if (dict != null)
                {
                    foreach (var kv in dict) _map[kv.Key] = kv.Value;
                }
            }
            catch
            {
                // ignore errors for now (best-effort)
            }
        }

        public async Task SaveAsync()
        {
            try
            {
                var tmp = _path + ".tmp";
                var json = System.Text.Json.JsonSerializer.Serialize(_map.ToDictionary(k => k.Key, v => v.Value));
                await File.WriteAllTextAsync(tmp, json).ConfigureAwait(false);
                File.Copy(tmp, _path, true);
                File.Delete(tmp);
            }
            catch
            {
                // swallow for now
            }
        }

        public Task SetAsync(string filename, string displayName)
        {
            _map[filename] = displayName;
            return SaveAsync();
        }

        public Task<string?> GetAsync(string filename)
        {
            return Task.FromResult(_map.TryGetValue(filename, out var val) ? val : null);
        }
    }

    /// <summary>
    /// Minimal parser stub — implement the profile parsing and manifest parsing here.
    /// </summary>
    public sealed class ModParser
    {
        private readonly BotConfig _config;
        private readonly ModCache _cache;

        public ModParser(BotConfig config, ModCache cache)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _cache = cache ?? throw new ArgumentNullException(nameof(cache));
        }

        public static string HexToReadableName(string hex)
        {
            if (string.IsNullOrWhiteSpace(hex)) return string.Empty;
            try
            {
                if (hex.Length % 2 != 0) hex = "0" + hex;
                var bytes = Enumerable.Range(0, hex.Length / 2)
                    .Select(i => Convert.ToByte(hex.Substring(i * 2, 2), 16))
                    .ToArray();
                var s = Encoding.UTF8.GetString(bytes);
                return s;
            }
            catch
            {
                return string.Empty;
            }
        }

        public static string CleanFilename(string filename)
        {
            // Basic cleanup to match original behavior
            if (string.IsNullOrWhiteSpace(filename)) return filename ?? string.Empty;
            var withoutExt = filename.EndsWith(".scs", StringComparison.OrdinalIgnoreCase)
                ? filename.Substring(0, filename.Length - 4)
                : filename;
            withoutExt = withoutExt.Replace('_', ' ').Replace('-', ' ');
            // Title-case
            return System.Globalization.CultureInfo.InvariantCulture.TextInfo.ToTitleCase(withoutExt.ToLowerInvariant());
        }

        public static List<ModInfo> ExtractModsFromContent(string content)
        {
            // Simple parser that finds lines like: active_mods[index]: "filename|Display Name"
            var list = new List<(int idx, string filename, string display)>();
            if (string.IsNullOrEmpty(content)) return new List<ModInfo>();

            using var sr = new StringReader(content);
            string? line;
            while ((line = sr.ReadLine()) != null)
            {
                var trimmed = line.Trim();
                // naive parse
                if (trimmed.StartsWith("active_mods[", StringComparison.OrdinalIgnoreCase))
                {
                    try
                    {
                        var colon = trimmed.IndexOf(':');
                        if (colon < 0) continue;
                        var left = trimmed.Substring(0, colon);
                        var idxStart = left.IndexOf('[');
                        var idxEnd = left.IndexOf(']');
                        var idxStr = left.Substring(idxStart + 1, idxEnd - idxStart - 1);
                        if (!int.TryParse(idxStr, out int idx)) continue;
                        var q1 = trimmed.IndexOf('"');
                        var q2 = trimmed.LastIndexOf('"');
                        if (q1 >= 0 && q2 > q1)
                        {
                            var payload = trimmed.Substring(q1 + 1, q2 - q1 - 1);
                            var parts = payload.Split('|', 2);
                            var fname = parts.Length > 0 ? parts[0] : string.Empty;
                            var disp = parts.Length > 1 ? parts[1] : CleanFilename(fname);
                            list.Add((idx, fname, disp));
                        }
                    }
                    catch
                    {
                        // ignore malformed lines
                    }
                }
            }

            // sort reverse by index (so the highest index comes first) to match older tests' expectations
            return list.OrderByDescending(x => x.idx)
                       .Select(x => new ModInfo(x.filename, x.display, x.idx))
                       .ToList();
        }

        // Add further parsing methods (manifest parsing, folder scan, workshop lookup) here.
    }

    /// <summary>
    /// Simple cooldown manager with per-user and global cooldowns.
    /// </summary>
    public sealed class CooldownManager
    {
        private readonly TimeSpan _userCooldown;
        private readonly TimeSpan _globalCooldown;
        private readonly ConcurrentDictionary<string, DateTime> _userTimestamps = new();
        private readonly ConcurrentDictionary<string, DateTime> _globalTimestamps = new();

        public CooldownManager(int userCooldown = 10, int globalCooldown = 2)
        {
            _userCooldown = TimeSpan.FromSeconds(userCooldown);
            _globalCooldown = TimeSpan.FromSeconds(globalCooldown);
        }

        /// <summary>
        /// Check if a user/command can run now. Returns (true, null) if ok; otherwise (false, message).
        /// </summary>
        public (bool ok, string? message) CheckCooldown(string user, string command)
        {
            if (string.IsNullOrEmpty(user)) user = "unknown";

            var now = DateTime.UtcNow;

            // user-specific cooldown (key: user+command)
            var userKey = $"{user}:{command}";
            if (_userTimestamps.TryGetValue(userKey, out var lastUser))
            {
                var diff = now - lastUser;
                if (diff < _userCooldown)
                {
                    var wait = (_userCooldown - diff).TotalSeconds;
                    return (false, $"Please wait {Math.Ceiling(wait)}s before using this command.");
                }
            }

            // global cooldown for this command
            if (_globalTimestamps.TryGetValue(command, out var lastGlobal))
            {
                var diffG = now - lastGlobal;
                if (diffG < _globalCooldown)
                {
                    var wait = (_globalCooldown - diffG).TotalSeconds;
                    return (false, $"Please wait {Math.Ceiling(wait)}s (global cooldown).");
                }
            }

            // record usage
            _userTimestamps[userKey] = now;
            _globalTimestamps[command] = now;
            return (true, null);
        }
    }

    /// <summary>
    /// Minimal single-instance lock for Windows (file-based).
    /// This is a best-effort cross-process guard; expand for robust behavior on all platforms.
    /// </summary>
    public sealed class SingleInstanceLock : IDisposable
    {
        private readonly string _lockPath;
        private FileStream? _handle;

        public SingleInstanceLock(string? lockFile = null)
        {
            _lockPath = lockFile ?? Path.Combine(Path.GetTempPath(), "ets2-twitch-mod-bot.lock");
        }

        public async Task AcquireAsync()
        {
            // If file exists, try to check contents; if it's stale, remove it.
            try
            {
                if (File.Exists(_lockPath))
                {
                    // best-effort cleanup if content not a current PID
                    var txt = await File.ReadAllTextAsync(_lockPath).ConfigureAwait(false);
                    if (!int.TryParse(txt.Trim(), out int pid) || pid != Environment.ProcessId)
                    {
                        try { File.Delete(_lockPath); } catch { }
                    }
                }

                _handle = new FileStream(_lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
                var pidStr = Environment.ProcessId.ToString();
                var bytes = Encoding.UTF8.GetBytes(pidStr);
                _handle.SetLength(0);
                await _handle.WriteAsync(bytes, 0, bytes.Length).ConfigureAwait(false);
                await _handle.FlushAsync().ConfigureAwait(false);
            }
            catch (IOException)
            {
                // Another process may hold the lock — signal via exception or exit.
                throw new InvalidOperationException("Another instance appears to be running.");
            }
        }

        public void Release()
        {
            try
            {
                _handle?.Dispose();
                if (File.Exists(_lockPath))
                {
                    File.Delete(_lockPath);
                }
            }
            catch
            {
                // ignore
            }
        }

        public void Dispose() => Release();
    }

    /// <summary>
    /// DLC detection stub. Implement scanning of steam/profiles folders to detect enabled DLCs.
    /// </summary>
    public sealed class DLCDetector
    {
        private readonly BotConfig _config;

        public DLCDetector(BotConfig config)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
        }

        public Task<List<string>> GetActiveDlcAsync()
        {
            // minimal implementation: scan steam path for dlc_*.scs files and return base names
            var outList = new List<string>();
            try
            {
                if (!string.IsNullOrWhiteSpace(_config.Ets2SteamPath) && Directory.Exists(_config.Ets2SteamPath))
                {
                    var dir = new DirectoryInfo(_config.Ets2SteamPath);
                    var files = dir.EnumerateFiles("dlc_*.scs", SearchOption.TopDirectoryOnly);
                    foreach (var f in files)
                    {
                        outList.Add(Path.GetFileNameWithoutExtension(f.Name));
                    }
                }
            }
            catch
            {
                // ignore and return what we have
            }
            return Task.FromResult(outList);
        }
    }

    /// <summary>
    /// Minimal bot core stub to host formatting and chunked messaging logic.
    /// Full Twitch client wiring (TwitchLib) should be implemented in the App project.
    /// </summary>
    public sealed class ETS2ModBot
    {
        private readonly BotConfig _config;

        public ETS2ModBot(BotConfig config)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
        }

        /// <summary>
        /// Format the response for a list of mods and DLCs into a single string message.
        /// </summary>
        public string FormatResponse(IEnumerable<ModInfo> mods, IEnumerable<string> dlcs)
        {
            var sb = new StringBuilder();
            var modList = mods?.ToList() ?? new List<ModInfo>();
            var dlcList = dlcs?.ToList() ?? new List<string>();

            if (!modList.Any() && !dlcList.Any())
            {
                sb.Append("No mods or DLC detected.");
                return sb.ToString();
            }

            if (modList.Any())
            {
                sb.Append("Mods: ");
                int i = 1;
                foreach (var m in modList)
                {
                    var name = m.DisplayName;
                    if (name.Length > 30) name = name.Substring(0, 27) + "...";
                    sb.Append($"{i}.{name} ");
                    i++;
                }
            }

            if (dlcList.Any())
            {
                if (sb.Length > 0) sb.Append(" | ");
                sb.Append("DLC: ");
                sb.Append(string.Join(", ", dlcList));
            }

            return sb.ToString();
        }

        /// <summary>
        /// Send a large message in chunks by calling the provided sendFunc for each chunk.
        /// Splits on friendly separators first, otherwise falls back to fixed-size chunks.
        /// </summary>
        public async Task SendChunkedMessageAsync(Func<string, Task> sendFunc, string message, int limit = 500, double delaySeconds = 0.5, CancellationToken cancellation = default)
        {
            if (sendFunc == null) throw new ArgumentNullException(nameof(sendFunc));
            if (string.IsNullOrEmpty(message))
            {
                await sendFunc(string.Empty).ConfigureAwait(false);
                return;
            }

            // Try splitting on separators for nicer chunks
            var separators = new[] { " || ", " | ", ", " " };
            var parts = new List<string> { message };

            foreach (var sep in separators)
            {
                var newParts = new List<string>();
                foreach (var p in parts)
                {
                    if (p.Length > limit && p.Contains(sep))
                    {
                        newParts.AddRange(p.Split(new string[] { sep }, StringSplitOptions.None));
                    }
                    else
                    {
                        newParts.Add(p);
                    }
                }
                parts = newParts;
            }

            // Emit chunks
            foreach (var part in parts)
            {
                if (cancellation.IsCancellationRequested) break;
                var trimmed = part.Trim();
                if (trimmed.Length == 0) continue;

                if (trimmed.Length <= limit)
                {
                    await sendFunc(trimmed).ConfigureAwait(false);
                }
                else
                {
                    // chunk fixed-size
                    for (int i = 0; i < trimmed.Length; i += limit)
                    {
                        if (cancellation.IsCancellationRequested) break;
                        var chunk = trimmed.Substring(i, Math.Min(limit, trimmed.Length - i));
                        await sendFunc(chunk).ConfigureAwait(false);
                    }
                }

                if (delaySeconds > 0)
                {
                    try { await Task.Delay(TimeSpan.FromSeconds(delaySeconds), cancellation).ConfigureAwait(false); } catch { }
                }
            }
        }
    }
}

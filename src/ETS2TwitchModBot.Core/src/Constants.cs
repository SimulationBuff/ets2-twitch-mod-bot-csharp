using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace ETS2TwitchModBot.Core
{
    /// <summary>
    /// Repository of commonly used constants across the core library.
    /// </summary>
    public static class Constants
    {
        /// <summary>
        /// Mapping from DLC code (as used in ETS2 files) to human-readable name.
        /// Matches values used by the original Python project.
        /// </summary>
        public static readonly IReadOnlyDictionary<string, string> MAJOR_MAP_DLC =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "east", "Going East!" },
                { "north", "Scandinavia" },
                { "fr", "Vive la France!" },
                { "it", "Italia" },
                { "balt", "Beyond the Baltic Sea" },
                { "iberia", "Iberia" },
                { "balkan_w", "West Balkans" },
                { "greece", "Greece" },
            };
    }

    /// <summary>
    /// Parser utilities for ETS2 mods, profiles and manifests.
    /// Encapsulates logic to extract active mods from profile content,
    /// parse manifest.sii files inside .scs archives and derive readable names.
    /// </summary>
    public sealed class ModParser
    {
        private readonly BotConfig _config;
        private readonly ModCache _cache;
        private readonly WorkshopLookup? _workshopLookup;

        private static readonly Regex ActiveModLineRegex =
            new Regex(@"active_mods\[(\d+)\]\s*:\s*""([^""]+)""", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex ModNameInManifestRegex =
            new Regex(@"mod_name\s*:\s*""(?<name>[^""]+)""", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        public ModParser(BotConfig config, ModCache cache, WorkshopLookup? workshopLookup = null)
        {
            _config = config ?? throw new ArgumentNullException(nameof(config));
            _cache = cache ?? throw new ArgumentNullException(nameof(cache));
            _workshopLookup = workshopLookup;
        }

        /// <summary>
        /// Convert an ASCII hex string (e.g. "4a6f686e") into a readable UTF-8 string when possible.
        /// Returns empty string on failure.
        /// </summary>
        public static string HexToReadableName(string hex)
        {
            if (string.IsNullOrWhiteSpace(hex)) return string.Empty;
            try
            {
                // Normalize even-length
                var cleaned = hex.Trim();
                if (cleaned.Length % 2 == 1) cleaned = "0" + cleaned;

                var bytes = new byte[cleaned.Length / 2];
                for (int i = 0; i < bytes.Length; i++)
                {
                    bytes[i] = Convert.ToByte(cleaned.Substring(i * 2, 2), 16);
                }

                var s = Encoding.UTF8.GetString(bytes);
                // Remove non-printable characters
                s = Regex.Replace(s, @"[^\u0020-\u007E]+", "");
                return s;
            }
            catch
            {
                return string.Empty;
            }
        }

        /// <summary>
        /// Clean a filename into a human-readable display name.
        /// Example: "cool_mod_v1.2_by_author.scs" -> "Cool Mod V1.2 by Author"
        /// </summary>
        public static string CleanFilename(string filename)
        {
            if (string.IsNullOrWhiteSpace(filename)) return string.Empty;
            var name = Path.GetFileNameWithoutExtension(filename);
            name = name.Replace('_', ' ').Replace('-', ' ');
            name = Regex.Replace(name, @"\s{2,}", " ").Trim();

            // Preserve " by " segments (try to title-case separately)
            var parts = Regex.Split(name, @"\bby\b", RegexOptions.IgnoreCase);
            if (parts.Length >= 2)
            {
                var left = ToTitleCase(parts[0].Trim());
                var right = ToTitleCase(parts[1].Trim());
                return $"{left} by {right}";
            }

            return ToTitleCase(name);
        }

        private static string ToTitleCase(string s)
        {
            if (string.IsNullOrWhiteSpace(s)) return s;
            var ti = System.Globalization.CultureInfo.InvariantCulture.TextInfo;
            return ti.ToTitleCase(s.ToLowerInvariant());
        }

        /// <summary>
        /// Parse active mods from a profile file content.
        /// Returns list ordered with highest indices first (reverse order) to match previous behavior.
        /// Each active_mods entry is expected in form:
        ///   active_mods[0]: "mod_filename|Display Name"
        /// </summary>
        public static List<ModInfo> ExtractModsFromContent(string content)
        {
            var found = new List<(int index, string filename, string display)>();
            if (string.IsNullOrWhiteSpace(content)) return new List<ModInfo>();

            foreach (Match m in ActiveModLineRegex.Matches(content))
            {
                if (!int.TryParse(m.Groups[1].Value, out var idx)) continue;
                var payload = m.Groups[2].Value;
                var parts = payload.Split(new[] { '|' }, 2);
                var filename = parts.Length > 0 ? parts[0] : string.Empty;
                var display = parts.Length > 1 ? parts[1] : CleanFilename(filename);
                found.Add((idx, filename, display));
            }

            // Sort descending by index (reverse load order) to match original tests' expectation
            return found.OrderByDescending(x => x.index)
                        .Select(x => new ModInfo(x.filename, x.display, x.index))
                        .ToList();
        }

        /// <summary>
        /// Attempt to parse a manifest.sii inside a .scs (zip) file to extract the mod_name.
        /// Returns null if no manifest or no name found.
        /// </summary>
        public static string? ParseManifestNameFromScs(string scsFilePath)
        {
            if (string.IsNullOrWhiteSpace(scsFilePath) || !File.Exists(scsFilePath)) return null;
            try
            {
                using var fs = File.OpenRead(scsFilePath);
                using var za = new ZipArchive(fs, ZipArchiveMode.Read, leaveOpen: false);

                // Fast-path: direct entry lookup (case-sensitive then lowercase)
                ZipArchiveEntry? entry = za.GetEntry("manifest.sii") ?? za.GetEntry("manifest.sii".ToLowerInvariant());

                // If direct lookup failed, search entries for any name or path that ends with "manifest.sii" (case-insensitive).
                if (entry == null)
                {
                    entry = za.Entries.FirstOrDefault(e =>
                        e.Name.Equals("manifest.sii", StringComparison.OrdinalIgnoreCase)
                        || e.FullName.EndsWith("/manifest.sii", StringComparison.OrdinalIgnoreCase)
                        || e.FullName.EndsWith("\\manifest.sii", StringComparison.OrdinalIgnoreCase));
                }

                if (entry == null) return null;

                using var sr = new StreamReader(entry.Open(), Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
                var content = sr.ReadToEnd();
                var m = ModNameInManifestRegex.Match(content);
                if (m.Success)
                {
                    return m.Groups["name"].Value.Trim();
                }
            }
            catch
            {
                // best-effort: ignore errors and return null
            }
            return null;
        }

        /// <summary>
        /// Parse mods from the configured mods folder.
        /// For each .scs file:
        ///  - try manifest.sii to get mod_name
        ///  - else consult cache
        ///  - else attempt Steam workshop lookup (if WorkshopLookup provided and SteamApiKey is set)
        ///  - else fallback to cleaned filename
        /// Persists any newly-resolved names into the cache.
        /// </summary>
        public async Task<List<ModInfo>> ParseFromFolderAsync(CancellationToken cancellationToken = default)
        {
            var list = new List<ModInfo>();
            var modsPath = _config.Ets2ModPath;
            if (string.IsNullOrWhiteSpace(modsPath) || !Directory.Exists(modsPath)) return list;

            var dir = new DirectoryInfo(modsPath);
            var files = dir.EnumerateFiles("*.scs", SearchOption.TopDirectoryOnly)
                           .OrderBy(f => f.Name, StringComparer.OrdinalIgnoreCase)
                           .ToArray();

            foreach (var f in files)
            {
                if (cancellationToken.IsCancellationRequested) break;
                var filename = f.Name;
                string? displayName = null;
                ModSource source = ModSource.Unknown;

                // 1) Try manifest
                try
                {
                    var manifestName = ParseManifestNameFromScs(f.FullName);
                    if (!string.IsNullOrWhiteSpace(manifestName))
                    {
                        displayName = manifestName;
                        source = ModSource.Manifest;
                        // persist manifest resolution to cache (best-effort)
                        try { await _cache.SetAsync(filename, displayName).ConfigureAwait(false); } catch { }
                    }
                }
                catch
                {
                    // ignore manifest errors and continue
                }

                // 2) Try cache (only if manifest didn't yield a name)
                if (string.IsNullOrWhiteSpace(displayName))
                {
                    var cached = await _cache.GetAsync(filename).ConfigureAwait(false);
                    if (!string.IsNullOrWhiteSpace(cached))
                    {
                        displayName = cached;
                        source = ModSource.Cache;
                    }
                }

                // 3) Try workshop lookup (if available and config provides SteamApiKey)
                if (string.IsNullOrWhiteSpace(displayName) && _workshopLookup != null && !string.IsNullOrWhiteSpace(_config.SteamApiKey))
                {
                    // Heuristic: some workshop-exported .scs names may include the published file id (rare).
                    // As a pragmatic approach, if the filename (without ext) is numeric treat it as an id.
                    var idCandidate = Path.GetFileNameWithoutExtension(filename);
                    if (!string.IsNullOrWhiteSpace(idCandidate) && idCandidate.All(char.IsDigit))
                    {
                        try
                        {
                            var name = await _workshopLookup.GetWorkshopItemNameAsync(idCandidate, _config.SteamApiKey).ConfigureAwait(false);
                            if (!string.IsNullOrWhiteSpace(name))
                            {
                                displayName = name;
                                source = ModSource.Workshop;
                                // persist to cache
                                try { await _cache.SetAsync(filename, displayName).ConfigureAwait(false); } catch { }
                            }
                        }
                        catch
                        {
                            // ignore and fallback
                        }
                    }
                }

                // 4) Fallback to cleaned filename
                if (string.IsNullOrWhiteSpace(displayName))
                {
                    displayName = CleanFilename(filename);
                    source = ModSource.Filename;
                    // persist to cache (best-effort)
                    try
                    {
                        await _cache.SetAsync(filename, displayName).ConfigureAwait(false);
                    }
                    catch
                    {
                        // ignore
                    }
                }

                var mod = new ModInfo(filename, displayName, loadOrder: 0, source: source, filePath: f.FullName);
                list.Add(mod);
            }

            return list;
        }

        /// <summary>
        /// Parse a profile (.sii) file using the SIIDecryptor and convert to ProfileInfo with ActiveMods.
        /// Returns null on failure to read/decrypt.
        /// </summary>
        public async Task<ProfileInfo?> ParseProfileAsync(string profileFilePath, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(profileFilePath) || !File.Exists(profileFilePath)) return null;

            var content = await SIIDecryptor.DecryptFileAsync(profileFilePath).ConfigureAwait(false);
            if (content == null) return null;

            var mods = ExtractModsFromContent(content);
            // Build ProfileInfo. The ProfileName is the folder name if the file is inside a profile folder,
            // otherwise fallback to the filename.
            var profileName = Path.GetFileName(Path.GetDirectoryName(profileFilePath) ?? Path.GetFileNameWithoutExtension(profileFilePath));
            var p = new ProfileInfo(profileName ?? "unknown", mods, rawContent: content);
            return p;
        }

        /// <summary>
        /// Scan the configured profiles folder and attempt to parse all discovered profile files (.sii).
        /// This method is tolerant of multiple layout variations:
        ///  - top-level .sii files directly in the profiles folder
        ///  - profile subfolders containing .sii files (common Steam layout)
        /// Returns a list of successfully parsed ProfileInfo objects.
        /// </summary>
        public async Task<List<ProfileInfo>> ParseProfilesFromFolderAsync(CancellationToken cancellationToken = default)
        {
            var outList = new List<ProfileInfo>();
            var profilesPath = _config.Ets2ProfilePath;
            if (string.IsNullOrWhiteSpace(profilesPath) || !Directory.Exists(profilesPath)) return outList;

            var dir = new DirectoryInfo(profilesPath);

            // Collect candidate .sii files (top-level and first-level subfolders)
            var candidates = new List<FileInfo>();
            try
            {
                candidates.AddRange(dir.EnumerateFiles("*.sii", SearchOption.TopDirectoryOnly));
            }
            catch
            {
                // ignore and continue
            }

            try
            {
                foreach (var sub in dir.EnumerateDirectories("*", SearchOption.TopDirectoryOnly))
                {
                    try
                    {
                        candidates.AddRange(sub.EnumerateFiles("*.sii", SearchOption.TopDirectoryOnly));
                    }
                    catch
                    {
                        // ignore broken folders/permission issues
                    }
                }
            }
            catch
            {
                // ignore enumeration issues
            }

            // Deduplicate by full path and sort for predictable ordering
            var distinctCandidates = candidates
                .GroupBy(f => f.FullName, StringComparer.OrdinalIgnoreCase)
                .Select(g => g.First())
                .OrderBy(f => f.FullName, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            foreach (var f in distinctCandidates)
            {
                if (cancellationToken.IsCancellationRequested) break;
                try
                {
                    var parsed = await ParseProfileAsync(f.FullName, cancellationToken).ConfigureAwait(false);
                    if (parsed != null)
                    {
                        outList.Add(parsed);
                    }
                }
                catch
                {
                    // best-effort: skip files that fail to parse/decrypt
                }
            }

            return outList;
        }
    }
}

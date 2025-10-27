using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace ETS2TwitchModBot.Core
{
    /// <summary>
    /// The origin/source of a mod's display name.
    /// </summary>
    public enum ModSource
    {
        Unknown = 0,
        Manifest = 1,
        Workshop = 2,
        Cache = 3,
        Filename = 4
    }

    /// <summary>
    /// Lightweight data holder describing a single mod entry.
    /// </summary>
    public sealed record ModInfo
    {
        /// <summary>
        /// The filename of the mod file (e.g. "cool_mod_v1.scs").
        /// This is used as the cache key and should be unique per mod.
        /// </summary>
        public string Filename { get; init; }

        /// <summary>
        /// The human readable display name for the mod.
        /// </summary>
        public string DisplayName { get; init; }

        /// <summary>
        /// Numeric load order. Lower numbers are loaded earlier.
        /// </summary>
        public int LoadOrder { get; init; }

        /// <summary>
        /// Where the display name was sourced from (manifest, workshop, filename, etc).
        /// </summary>
        public ModSource Source { get; init; }

        /// <summary>
        /// Optional path to the .scs file on disk. This may be null for workshop-only entries.
        /// </summary>
        public string? FilePath { get; init; }

        public ModInfo(string filename, string displayName, int loadOrder = 0, ModSource source = ModSource.Unknown, string? filePath = null)
        {
            if (string.IsNullOrWhiteSpace(filename)) throw new ArgumentException("filename must be provided", nameof(filename));
            Filename = filename;
            DisplayName = displayName ?? throw new ArgumentNullException(nameof(displayName));
            LoadOrder = loadOrder;
            Source = source;
            FilePath = filePath;
        }

        /// <summary>
        /// Returns a new instance with the display name cleaned/normalized from a raw manifest or filename.
        /// </summary>
        public ModInfo WithCleanDisplayName()
        {
            var cleaned = CleanDisplayName(DisplayName);
            return this with { DisplayName = cleaned };
        }

        /// <summary>
        /// Attempt to derive a readable name from a file name if a user-friendly name is not available.
        /// This implements similar heuristics to the original Python implementation:
        /// - Remove file extension (.scs)
        /// - Replace underscores and dashes with spaces
        /// - Normalize common separators and capitalization
        /// - Preserve existing 'By' or 'by' segments where possible
        /// </summary>
        public static string CleanDisplayName(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw)) return raw;

            // If the raw looks like hex (only hex digits) attempt to decode
            if (Regex.IsMatch(raw, @"\A[0-9a-fA-F]{6,}\z"))
            {
                var decoded = HexToStringSafe(raw);
                if (!string.IsNullOrWhiteSpace(decoded))
                {
                    return ToTitleCasePreservingCase(decoded);
                }
            }

            // Remove common file extension
            var withoutExt = Regex.Replace(raw, @"\.scs\z", "", RegexOptions.IgnoreCase);

            // Replace separators
            var replaced = withoutExt.Replace('_', ' ').Replace('-', ' ');

            // Collapse multiple spaces
            replaced = Regex.Replace(replaced, @"\s{2,}", " ").Trim();

            // If string contains ' by ' (e.g. filename_by_author) try to keep case for author piece
            var byMatch = Regex.Match(replaced, @"\bby\b", RegexOptions.IgnoreCase);
            if (byMatch.Success)
            {
                // Split on first " by " occurrence to preserve author's case
                var parts = Regex.Split(replaced, @"\bby\b", RegexOptions.IgnoreCase, TimeSpan.FromMilliseconds(50));
                if (parts.Length >= 2)
                {
                    var left = ToTitleCasePreservingCase(parts[0].Trim());
                    var right = ToTitleCasePreservingCase(parts[1].Trim());
                    return $"{left} by {right}";
                }
            }

            // Default: title case the whole thing
            return ToTitleCasePreservingCase(replaced);
        }

        /// <summary>
        /// Try to convert a hex string (ASCII bytes encoded as hex) to a readable string.
        /// Returns null if conversion fails.
        /// </summary>
        private static string? HexToStringSafe(string hex)
        {
            try
            {
                if (hex.Length % 2 != 0) hex = "0" + hex; // pad odd length
                var bytes = new byte[hex.Length / 2];
                for (int i = 0; i < bytes.Length; i++)
                {
                    bytes[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
                }
                // Interpret as UTF-8 and strip non-printable characters
                var str = Encoding.UTF8.GetString(bytes);
                str = Regex.Replace(str, @"[^\u0020-\u007E]+", ""); // basic ASCII printable range
                return string.IsNullOrWhiteSpace(str) ? null : str;
            }
            catch
            {
                return null;
            }
        }

        private static string ToTitleCasePreservingCase(string input)
        {
            if (string.IsNullOrWhiteSpace(input)) return input;
            // If input is already mixed-case with capitals inside words, prefer it.
            if (Regex.IsMatch(input, @"[A-Z].*[a-z]"))
            {
                return input.Trim();
            }

            // Use the invariant culture title-casing to produce readable names.
            var ti = CultureInfo.InvariantCulture.TextInfo;
            var lowered = input.ToLowerInvariant();
            var titled = ti.ToTitleCase(lowered);
            return Regex.Replace(titled, @"\s{2,}", " ").Trim();
        }

        public override string ToString() => $"{DisplayName} ({Filename})";
    }

    /// <summary>
    /// Represents a parsed ETS2 profile, including its name and active mods in load order.
    /// </summary>
    public sealed record ProfileInfo
    {
        /// <summary>
        /// Name of the profile (folder name under the profiles directory).
        /// </summary>
        public string ProfileName { get; init; }

        /// <summary>
        /// Raw text content (if available) of the profile's .sii or config file.
        /// </summary>
        public string? RawContent { get; init; }

        /// <summary>
        /// List of active mods parsed from the profile, in load order (first = highest priority)
        /// </summary>
        public IReadOnlyList<ModInfo> ActiveMods { get; init; }

        public ProfileInfo(string profileName, IEnumerable<ModInfo>? activeMods = null, string? rawContent = null)
        {
            ProfileName = profileName ?? throw new ArgumentNullException(nameof(profileName));
            ActiveMods = (activeMods ?? Enumerable.Empty<ModInfo>()).ToArray();
            RawContent = rawContent;
        }

        /// <summary>
        /// Return a new ProfileInfo with mods sorted by LoadOrder ascending.
        /// </summary>
        public ProfileInfo WithSortedMods()
        {
            var sorted = ActiveMods.OrderBy(m => m.LoadOrder).ToArray();
            return this with { ActiveMods = sorted };
        }

        public override string ToString()
        {
            var names = ActiveMods?.Select(m => m.DisplayName).Take(10);
            var joined = names is null ? string.Empty : string.Join(", ", names);
            return $"{ProfileName}: {joined}";
        }
    }
}

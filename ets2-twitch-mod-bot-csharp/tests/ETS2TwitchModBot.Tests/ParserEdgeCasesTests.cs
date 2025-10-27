/dev/null/ets2-twitch-mod-bot/ets2-twitch-mod-bot-csharp/tests/ETS2TwitchModBot.Tests/ParserEdgeCasesTests.cs#L1-240
using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using ETS2TwitchModBot.Core;
using FluentAssertions;
using Xunit;

namespace ETS2TwitchModBot.Tests
{
    public class ParserEdgeCasesTests
    {
        [Fact]
        public void HexToReadableName_ShouldDecodeSimpleAsciiHex()
        {
            // "John" -> 4a6f686e
            var hex = "4a6f686e";
            var result = ModParser.HexToReadableName(hex);

            result.Should().Be("John");
        }

        [Fact]
        public void CleanDisplayName_ShouldDecodeHexFilenames()
        {
            // Some mods use hex-encoded filenames; ensure our filename cleaning decodes them.
            var raw = "4a6f686e.scs"; // "John"
            var cleaned = ModInfo.CleanDisplayName(raw);

            cleaned.Should().Be("John");
        }

        [Fact]
        public void ParseManifestNameFromScs_NoModName_ReturnsNull()
        {
            var tempFile = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString() + ".scs");
            try
            {
                using (var zf = new FileStream(tempFile, FileMode.CreateNew, FileAccess.Write))
                using (var za = new ZipArchive(zf, ZipArchiveMode.Create, leaveOpen: false))
                {
                    var entry = za.CreateEntry("manifest.sii");
                    using var es = entry.Open();
                    using var sw = new StreamWriter(es, Encoding.UTF8);
                    // no mod_name present
                    sw.Write("some_other_key: \"Value\"\n");
                }

                var name = ModParser.ParseManifestNameFromScs(tempFile);
                name.Should().BeNull();
            }
            finally
            {
                try { if (File.Exists(tempFile)) File.Delete(tempFile); } catch { }
            }
        }

        [Fact]
        public void ParseManifestNameFromScs_FindsManifestInSubfolderAndCaseInsensitive()
        {
            var tempFile = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString() + ".scs");
            try
            {
                using (var zf = new FileStream(tempFile, FileMode.CreateNew, FileAccess.Write))
                using (var za = new ZipArchive(zf, ZipArchiveMode.Create, leaveOpen: false))
                {
                    // Put manifest in a nested folder and use different casing to test resilience
                    var entry = za.CreateEntry("nested/MANIFEST.SII");
                    using var es = entry.Open();
                    using var sw = new StreamWriter(es, Encoding.UTF8);
                    sw.Write("mod_name: \"Nested Mod\"\n");
                }

                var name = ModParser.ParseManifestNameFromScs(tempFile);
                name.Should().Be("Nested Mod");
            }
            finally
            {
                try { if (File.Exists(tempFile)) File.Delete(tempFile); } catch { }
            }
        }

        [Fact]
        public void ParseManifestNameFromScs_MultipleModNameEntries_ReturnsFirst()
        {
            var tempFile = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString() + ".scs");
            try
            {
                using (var zf = new FileStream(tempFile, FileMode.CreateNew, FileAccess.Write))
                using (var za = new ZipArchive(zf, ZipArchiveMode.Create, leaveOpen: false))
                {
                    var entry = za.CreateEntry("manifest.sii");
                    using var es = entry.Open();
                    using var sw = new StreamWriter(es, Encoding.UTF8);
                    sw.Write("mod_name: \"First Name\"\n");
                    sw.Write("mod_name: \"Second Name\"\n");
                }

                var name = ModParser.ParseManifestNameFromScs(tempFile);
                name.Should().Be("First Name");
            }
            finally
            {
                try { if (File.Exists(tempFile)) File.Delete(tempFile); } catch { }
            }
        }

        [Fact]
        public void ParseManifestNameFromScs_HandlesManifestWithExtraWhitespaceAndQuotes()
        {
            var tempFile = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString() + ".scs");
            try
            {
                using (var zf = new FileStream(tempFile, FileMode.CreateNew, FileAccess.Write))
                using (var za = new ZipArchive(zf, ZipArchiveMode.Create, leaveOpen: false))
                {
                    var entry = za.CreateEntry("manifest.sii");
                    using var es = entry.Open();
                    using var sw = new StreamWriter(es, Encoding.UTF8);
                    // odd whitespace and tabs should still be parsed
                    sw.Write("   mod_name\t:\t\"  Weird   Name  \"  \n");
                }

                var name = ModParser.ParseManifestNameFromScs(tempFile);
                // Trimmed value expected
                name.Should().Be("Weird   Name");
            }
            finally
            {
                try { if (File.Exists(tempFile)) File.Delete(tempFile); } catch { }
            }
        }
    }
}

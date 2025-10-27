using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using ETS2TwitchModBot.Core;
using FluentAssertions;
using Xunit;

namespace ETS2TwitchModBot.Tests
{
    public class DLCDetectorTests
    {
        [Fact]
        public async Task SteamPath_DetectsDlcFiles_ReturnsFriendlyNames()
        {
            var tmpDir = Path.Combine(Path.GetTempPath(), "ets2_dlc_test_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tmpDir);
            try
            {
                // Create a couple of dlc_*.scs files
                File.WriteAllText(Path.Combine(tmpDir, "dlc_east.scs"), "dummy");
                File.WriteAllText(Path.Combine(tmpDir, "dlc_fr.scs"), "dummy");

                var config = new BotConfig { Ets2SteamPath = tmpDir };
                var detector = new DLCDetector(config);

                var dlcs = await detector.GetActiveDlcAsync().ConfigureAwait(false);

                // Friendly names from Constants.MAJOR_MAP_DLC
                dlcs.Should().Contain("Going East!");
                dlcs.Should().Contain("Vive la France!");
            }
            finally
            {
                try { Directory.Delete(tmpDir, recursive: true); } catch { }
            }
        }

        [Fact]
        public async Task ProfileFiles_DetectsDlcTokens_ReturnsFriendlyAndRawCodes()
        {
            var tmpDir = Path.Combine(Path.GetTempPath(), "ets2_profile_dlc_test_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tmpDir);
            try
            {
                // Create a profile folder and a .sii file containing dlc tokens
                var profileFolder = Path.Combine(tmpDir, "profile1");
                Directory.CreateDirectory(profileFolder);

                // include a known token and an unknown one (should return 'foobar' for unknown)
                var siiContent = "some setting: 1\nactive_mods[0]: \"mod.scs|Mod\"\ndlc_east dlc_foobar\n";
                var siiPath = Path.Combine(profileFolder, "profile.sii");
                await File.WriteAllTextAsync(siiPath, siiContent).ConfigureAwait(false);

                var config = new BotConfig { Ets2ProfilePath = tmpDir };
                var detector = new DLCDetector(config);

                var dlcs = await detector.GetActiveDlcAsync().ConfigureAwait(false);

                // Known token maps to friendly name
                dlcs.Should().Contain("Going East!");
                // Unknown token should surface as the raw code ("foobar")
                dlcs.Should().Contain("foobar");
            }
            finally
            {
                try { Directory.Delete(tmpDir, recursive: true); } catch { }
            }
        }

        [Fact]
        public async Task NoPaths_ReturnsEmptyList()
        {
            var config = new BotConfig { Ets2SteamPath = Path.Combine(Path.GetTempPath(), "nonexistent_" + Guid.NewGuid()), Ets2ProfilePath = Path.Combine(Path.GetTempPath(), "also_nonexistent_" + Guid.NewGuid()) };
            var detector = new DLCDetector(config);

            var dlcs = await detector.GetActiveDlcAsync().ConfigureAwait(false);

            dlcs.Should().BeEmpty();
        }
    }
}

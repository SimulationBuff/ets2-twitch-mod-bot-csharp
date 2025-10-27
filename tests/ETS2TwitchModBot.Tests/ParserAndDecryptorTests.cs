using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using ETS2TwitchModBot.Core;
using FluentAssertions;
using Xunit;

namespace ETS2TwitchModBot.Tests
{
    public class ParserAndDecryptorTests
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
        public void CleanFilename_ShouldTransformFilenameToTitleCaseAndPreserveBy()
        {
            var filename = "cool_mod_v1.2_by_author.scs";
            var cleaned = ModParser.CleanFilename(filename);

            cleaned.Should().Be("Cool Mod V1.2 by Author");
        }

        [Fact]
        public void ExtractModsFromContent_ShouldParseAndOrderReverseByIndex()
        {
            var content = string.Join(
                Environment.NewLine,
                new[]
                {
                    "active_mods[0]: \"mod_a|Alpha Mod\"",
                    "active_mods[2]: \"mod_c|Charlie\"",
                    "active_mods[1]: \"mod_b|Bravo\""
                });

            var mods = ModParser.ExtractModsFromContent(content);

            mods.Should().HaveCount(3);
            mods.Select(m => m.Filename).Should().Equal("mod_c", "mod_b", "mod_a");
            mods.Select(m => m.DisplayName).Should().Equal("Charlie", "Bravo", "Alpha Mod");
        }

        [Fact]
        public void ParseManifestNameFromScs_ShouldExtractModNameFromManifest()
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
                    sw.Write("mod_name: \"Manifested Mod\"\n");
                }

                var name = ModParser.ParseManifestNameFromScs(tempFile);
                name.Should().Be("Manifested Mod");
            }
            finally
            {
                if (File.Exists(tempFile)) File.Delete(tempFile);
            }
        }

        [Fact]
        public async Task SIIDecryptor_DecryptsPlainSignatureFile()
        {
            var content = "some setting: 1\nactive_mods[0]: \"mod|Name\"\n";
            var tmp = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString() + ".sii");
            try
            {
                // Build file with normal signature then content bytes
                var sig = BitConverter.GetBytes(SIIConstants.SII_SIGNATURE_NORMAL);
                await File.WriteAllBytesAsync(tmp, sig.Concat(Encoding.UTF8.GetBytes(content)).ToArray());

                var result = await SIIDecryptor.DecryptFileAsync(tmp).ConfigureAwait(false);
                result.Should().NotBeNull();
                result.Should().Contain("active_mods");
                result.Should().Contain("some setting");
            }
            finally
            {
                if (File.Exists(tmp)) File.Delete(tmp);
            }
        }

        [Fact]
        public async Task SIIDecryptor_DecryptsEncryptedFile_ReturnsPlaintext()
        {
            // Prepare plaintext
            var originalText = "active_mods[0]: \"mod_a|Alpha\"\nactive_mods[1]: \"mod_b|Bravo\"\n";
            var tmp = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString() + ".sii");

            try
            {
                // We'll encrypt the plaintext with AES-256-CBC using the known SII key.
                // The decryptor will decrypt and return the UTF-8 text.
                byte[] plaintextBytes = Encoding.UTF8.GetBytes(originalText);

                // Create random IV
                byte[] iv = RandomNumberGenerator.GetBytes(16);

                byte[] encrypted;
                using (var aes = Aes.Create())
                {
                    aes.KeySize = 256;
                    aes.Key = SIIConstants.SII_KEY;
                    aes.IV = iv;
                    aes.Mode = CipherMode.CBC;
                    aes.Padding = PaddingMode.PKCS7;

                    using var encryptor = aes.CreateEncryptor();
                    encrypted = encryptor.TransformFinalBlock(plaintextBytes, 0, plaintextBytes.Length);
                }

                // Build header: 4-byte signature + 32 bytes (HMAC placeholder) + 16-byte IV + 4-byte datasize + payload
                var header = new byte[4 + 32 + 16 + 4];
                // signature LE
                Array.Copy(BitConverter.GetBytes(SIIConstants.SII_SIGNATURE_ENCRYPTED), 0, header, 0, 4);
                // 32-byte HMAC placeholder (zeros) already zeroed
                // IV at offset 36
                Array.Copy(iv, 0, header, 36, 16);
                // datasize (uint little-endian) at offset 52 (4+32+16 = 52)
                Array.Copy(BitConverter.GetBytes((uint)encrypted.Length), 0, header, 52, 4);

                // Write file
                using (var fs = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    await fs.WriteAsync(header, 0, header.Length).ConfigureAwait(false);
                    await fs.WriteAsync(encrypted, 0, encrypted.Length).ConfigureAwait(false);
                }

                var result = await SIIDecryptor.DecryptFileAsync(tmp).ConfigureAwait(false);
                result.Should().NotBeNull();
                result.Should().Contain("Alpha");
                result.Should().Contain("Bravo");
            }
            }
            finally
            {
                if (File.Exists(tmp)) File.Delete(tmp);
            }
        }

        [Fact]
        public async Task ModCache_ConcurrentSetGet_DoesNotThrowAndPersists()
        {
            var tmp = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString() + ".json");
            try
            {
                var cache = new ETS2TwitchModBot.Core.ModCache(tmp);
                await cache.LoadAsync().ConfigureAwait(false);

                // Spawn many concurrent tasks that perform SetAsync and GetAsync to simulate contention.
                var tasks = Enumerable.Range(0, 50).Select(i => Task.Run(async () =>
                {
                    var key = $"mod_{i % 5}.scs";
                    var value = $"Name_{i}";
                    await cache.SetAsync(key, value).ConfigureAwait(false);
                    var got = await cache.GetAsync(key).ConfigureAwait(false);
                    // Ensure we got something back (last-writer wins is acceptable)
                    got.Should().NotBeNull();
                })).ToArray();

                await Task.WhenAll(tasks).ConfigureAwait(false);

                // Reload cache from disk to ensure persistence survived concurrent writes
                var cache2 = new ETS2TwitchModBot.Core.ModCache(tmp);
                await cache2.LoadAsync().ConfigureAwait(false);

                for (int i = 0; i < 5; i++)
                {
                    var k = $"mod_{i}.scs";
                    var v = await cache2.GetAsync(k).ConfigureAwait(false);
                    v.Should().NotBeNull();
                }
            }
            finally
            {
                if (File.Exists(tmp)) File.Delete(tmp);
            }
        }
        [Fact]
        public async Task ParseProfilesFromFolderAsync_ParsesPlainProfile()
        {
            var tmpDir = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString());
            Directory.CreateDirectory(tmpDir);
            try
            {
                // Create a profile folder and a plain-text .sii file with the normal signature
                var profileFolder = Path.Combine(tmpDir, "profile1");
                Directory.CreateDirectory(profileFolder);
                var siiPath = Path.Combine(profileFolder, "profile.sii");

                var content = "some setting: 1\nactive_mods[0]: \"cool_mod.scs|Cool Mod\"\n";
                var sig = BitConverter.GetBytes(SIIConstants.SII_SIGNATURE_NORMAL);
                await File.WriteAllBytesAsync(siiPath, sig.Concat(Encoding.UTF8.GetBytes(content)).ToArray()).ConfigureAwait(false);

                var config = new BotConfig { Ets2ProfilePath = tmpDir };
                var cacheFile = Path.Combine(tmpDir, "cache.json");
                var cache = new ModCache(cacheFile);
                await cache.LoadAsync().ConfigureAwait(false);

                var parser = new ModParser(config, cache);
                var profiles = await parser.ParseProfilesFromFolderAsync().ConfigureAwait(false);

                profiles.Should().NotBeNull();
                profiles.Should().HaveCountGreaterOrEqualTo(1);
                var p = profiles.FirstOrDefault(pr => pr.ProfileName.Equals("profile1", StringComparison.OrdinalIgnoreCase));
                p.Should().NotBeNull();
                p!.ActiveMods.Should().ContainSingle();
                p.ActiveMods[0].DisplayName.Should().Contain("Cool Mod");
            }
            finally
            {
                try { Directory.Delete(tmpDir, true); } catch { }
            }
        }

        [Fact]
        public async Task ParseProfilesFromFolderAsync_ParsesEncryptedProfile()
        {
            var tmpDir = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString());
            Directory.CreateDirectory(tmpDir);
            try
            {
                // Create encrypted .sii file using the known SII key
                var profileFolder = Path.Combine(tmpDir, "profile_enc");
                Directory.CreateDirectory(profileFolder);
                var siiPath = Path.Combine(profileFolder, "profile.sii");

                var plaintext = "active_mods[0]: \"enc_mod.scs|Encrypted Mod\"\n";
                var plaintextBytes = Encoding.UTF8.GetBytes(plaintext);

                // Create random IV
                var iv = RandomNumberGenerator.GetBytes(16);
                byte[] encrypted;
                using (var aes = Aes.Create())
                {
                    aes.KeySize = 256;
                    aes.Key = SIIConstants.SII_KEY;
                    aes.IV = iv;
                    aes.Mode = CipherMode.CBC;
                    aes.Padding = PaddingMode.PKCS7;

                    using var encryptor = aes.CreateEncryptor();
                    encrypted = encryptor.TransformFinalBlock(plaintextBytes, 0, plaintextBytes.Length);
                }

                // Header: signature + 32-byte HMAC placeholder + IV + datasize
                var header = new byte[4 + 32 + 16 + 4];
                Array.Copy(BitConverter.GetBytes(SIIConstants.SII_SIGNATURE_ENCRYPTED), 0, header, 0, 4);
                // 32 bytes HMAC placeholder are left as zeros
                Array.Copy(iv, 0, header, 36, 16);
                Array.Copy(BitConverter.GetBytes((uint)encrypted.Length), 0, header, 52, 4);

                await File.WriteAllBytesAsync(siiPath, header.Concat(encrypted).ToArray()).ConfigureAwait(false);

                var config = new BotConfig { Ets2ProfilePath = tmpDir };
                var cacheFile = Path.Combine(tmpDir, "cache.json");
                var cache = new ModCache(cacheFile);
                await cache.LoadAsync().ConfigureAwait(false);

                var parser = new ModParser(config, cache);
                var profiles = await parser.ParseProfilesFromFolderAsync().ConfigureAwait(false);

                profiles.Should().NotBeNull();
                profiles.Should().HaveCountGreaterOrEqualTo(1);
                var p = profiles.FirstOrDefault(pr => pr.ProfileName.Equals("profile_enc", StringComparison.OrdinalIgnoreCase));
                p.Should().NotBeNull();
                p!.ActiveMods.Should().ContainSingle();
                p.ActiveMods[0].DisplayName.Should().Contain("Encrypted Mod");
            }
            finally
            {
                try { Directory.Delete(tmpDir, true); } catch { }
            }
        }
    }
}

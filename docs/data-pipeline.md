# Data pipeline

The repository keeps loose game data and client configuration. Packaging fetches
the binaries and optional movies, builds MIX archives, assembles the installation,
verifies it, and writes ZIPs or an updater mirror.

## Inputs

| Source | Contents |
| --- | --- |
| `assets/<name>/` | Members of `<NAME>.MIX` |
| `ini/` | Game configuration |
| `maps/` | Campaign and multiplayer maps |
| `client/` | Client configuration, resources, map previews and root files |
| `pins.toml` | Download URLs and SHA256 hashes for the engine, client, launcher, editor and movies |

Game data was imported from the installation recorded in `seed-report.json`.
The one-time `seed` tool verifies that original archives roundtrip byte for byte,
unpacks nested archives, and applies patch and Firestorm precedence. Overrides
are merged into the base archive groups; maps and configuration are kept loose.
Unrecovered filenames retain their archive IDs as `_id_xxxxxxxx.bin`.

The client configuration and presentation come from the TS Client package,
with OpenTS adaptations. [Client provenance](../client/NOTICE.md) identifies the source.

## Build steps

`python tools/build.py package` runs these steps:

1. **Fetch:** verify pinned downloads and extract them into the cache. An explicit
   engine selector, such as `--engine nightly`, replaces the engine release pin.
2. **Pack:** write each selected asset directory to `build/MIX/<NAME>.MIX`.
   Unchanged inputs reuse their archive; identical inputs produce identical bytes.
3. **Assemble:** copy the inputs into `dist/OpenTS-Client/` using the layout below.
4. **Verify:** check required game archives, client configuration and referenced resources.
5. **Bundle:** write the selected ZIPs under `dist/`, each containing `OpenTS-Client/`.

| Input | Installation path / precedence |
| --- | --- |
| Engine files | Root |
| Packed archives | `MIX/` |
| Downloaded client, then `client/Resources/` | `Resources/`; tracked files take precedence |
| Launcher and its `.config` | Root, renamed to `OpenTS.exe` and `OpenTS.exe.config` |
| WAE and its source/license notices from `editor/` | `MapEditor/` |
| `client/INI/`, then `ini/` | `INI/`; game configuration takes precedence |
| `maps/`, then `client/Maps/` | `Maps/`, including previews |
| `client/root/` | Root, copied last |

The engine reads `INI/battle.ini` and `INI/battlefs.ini`. The client uses
`INI/ClientBattle.ini`, selected by `IgnoreBattleIni=true` and `BattleFSFileName`.

`dev.cmd` uses the same client placement in `build/dev-client/`, omitting the
engine and MIX archives unless `-WithGame` is supplied. Preparation verifies files;
client-launched games additionally require an engine with spawner support.

WAE uses the standard upstream TS profile. Its default game directory is set
to `..`, relative to `MapEditor/`. Packages and `-WithGame` include it; client-only
preparation hides the editor controls. WAE needs .NET 8 Desktop Runtime.

## Movies

`assets/MOVIES00/` contains the startup movies and stays in every game package.
The campaign movies are downloaded into ignored `assets/MOVIES/` and packed as
`MOVIES.MIX`. `nomovies` omits that archive; `full` includes it; `both` builds both ZIPs.
Development preparation always omits it.

The original discs reuse `INTRO.VQA` for different factions. The imported data
keeps both as `INTR0.VQA` and `INTR1.VQA`. The `media` command creates the reproducible
movie ZIP named in `pins.toml`; this download is separate from the updater's MIX component.

## Updater and hosting

Adding `--tag <version> --mirror --previous <channel-url>` also builds
`dist/mirror/<version>/` and `dist/mirror/Components/MOVIES.MIX`.
The mirror's `version` inventories files and hashes; large files may be LZMA-compressed.
Its `updateexec` carries forward removal history so clients can skip versions.
Player settings (`SUN.ini`), the selected channel (`Resources/UpdaterConfig.ini`)
and WAE preferences (`MapEditor/MapEditorSettings.ini`) are excluded from updates.

CnCNet hosts released mirrors under `updates/games/opents/updates/<version>/`.
`live` points to a released version; `dev/` is replaced by each Dev deployment.
Both channels use the shared `updates/Components/MOVIES.MIX` component.

Mirror builds need the campaign movie bytes even for a no-movies distribution.
Dev can only upload its own channel folder: a changed movie component must be
uploaded by a release before Dev can serve it.

Releases save the verified mirror as an uncompressed workflow artifact. Approved
deployment uploads those exact bytes, checks CDN readback and moves Live for stable
releases. If Live's recorded update history changed, the release must be rebuilt.
Prereleases remain staged; promotion and rollback select an existing uploaded version.

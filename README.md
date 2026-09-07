# OpenTS client package

Game assets, client configuration and packaging for [OpenTS](https://github.com/OpenTS-Developers/OpenTS),
using the CnCNet client. The engine, client, launcher, map editor and campaign movies are
downloaded from the sources in `pins.toml`.

## Run the client

On Windows 10/11 x64:

```powershell
.\dev.cmd              # Prepare and launch the client
.\dev.cmd -PrepareOnly # Prepare without launching
.\dev.cmd -WithGame    # Also prepare the game and map editor
```

The script downloads portable Python and 7-Zip automatically and caches its
dependencies. No global installation is needed; the client requires .NET
Framework 4.8. The prepared client lives in `build/dev-client/`.

The default runs without the engine or campaign movies. `-WithGame` also
launches the client and still excludes campaign movies. Both switches can be combined.

Game packages and `-WithGame` include World-Altering Editor in `MapEditor/`,
available from the client's Map Editor button. WAE requires the .NET 8 Desktop
Runtime and a DirectX 11 GPU. The client-only preview leaves out the editor.

## Build a package

Use the Python version in `tools/.python-version` and a `7z` or `7zr` extractor:

```bash
python tools/build.py package
python tools/build.py package --variant both --tag v0.1.0
```

Packaging downloads dependencies, packs the game archives, assembles and
verifies the distribution, and produces ZIPs under `dist/`. The default is
`nomovies`; `full` includes campaign movies and `both` builds both variants.
Use `--engine nightly` to build with the engine's nightly version.

## CI

- **Build:** pull requests and pushes to `main` run tool tests and Windows
  client preparation. Pull requests also verify game archives; pushes produce
  a no-movies distribution artifact. A weekly run validates freshly downloaded movies.
- **Dev:** runs nightly or manually, builds against the selected engine and
  deploys to the Dev channel. Scheduled runs skip unchanged builds.
- **Release:** publishing a release builds both ZIPs and a verified update
  mirror. ZIPs are attached to GitHub; approved deployment uploads the saved
  mirror. Stable releases move Live, while prereleases remain staged.
- **Promote:** manually points Live at an uploaded version, for promotion or rollback.

Release deployment and promotion share a lock. Stable deployment stops if
Live's update history changed after the package was built.

See the [data pipeline](docs/data-pipeline.md) and [client provenance](client/NOTICE.md)
for details.

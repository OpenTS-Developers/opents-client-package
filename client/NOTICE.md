# Client asset attribution

The client configuration and presentation derive from
[CnCNet/cncnet-ts-client-package](https://github.com/CnCNet/cncnet-ts-client-package)
at commit `4b8b6e82388e414f58bcba32e3231a91cd6dd252`.

Selected material includes window layouts, themes and artwork, fonts, cursors,
icons, sounds, menu music, translations, campaign and multiplayer listings,
39 multiplayer map previews, and the seven `INI/Game Options/` rule files.

OpenTS adapts the branding, campaigns, maps, lobby options, hotkeys, translations
and update channels. Display settings target the native OpenTS renderer;
legacy DirectDraw wrappers, QRes, Vinifera configuration and quickmatch are
not included. Game data comes from this repository's `assets/`,
`ini/` and `maps/` trees rather than the upstream package's copies.

Client and launcher binaries are fetched separately through [`pins.toml`](../pins.toml)
and are not stored here. WorldAlteringEditor is a separate pinned dependency;
see [`editor/SOURCE.txt`](../editor/SOURCE.txt) for its source and licenses.
Legacy DTA identifiers remain for client compatibility.
[`root/credits.htm`](root/credits.htm) credits Rampastring, Dawn of the Tiberium
Age, CnCNet and the TS package contributors, with links to their full credits.

`Resources/clienticon.ico` comes from the engine's `code/resources/app-icon/opents.ico`;
`Resources/opentsicon.png` uses its 16-pixel frame. Menu and loading logos derive
from the OpenTS wordmark in `branding/opents-logo.png`. These generated assets
are tracked; the build does not require image-processing tools.

See the [data pipeline](../docs/data-pipeline.md) for packaging and hosting.

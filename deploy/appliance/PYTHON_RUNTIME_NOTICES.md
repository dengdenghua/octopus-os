# Echo OS appliance Python runtime notices

This file accompanies the Echo OS appliance image and its release evidence.
The image also retains each installed Python distribution's
`*.dist-info/licenses/` directory, and the platform SPDX SBOM records the
installed packages.

## pillow-heif 1.7.0

Echo OS uses `pillow-heif` to decode HEIC/HEIF photos for local previews. The
Python wrapper is BSD-3-Clause licensed. The upstream binary-wheel notice says
that the wheels as a whole are GPLv2 because they bundle the following native
libraries:

| Component | Version | License | Exact source |
| --- | --- | --- | --- |
| pillow-heif | 1.7.0 (`f65a9ac77809609ad8ebb001c691b5a3ee01146b`) | BSD-3-Clause; binary wheel GPLv2 | <https://github.com/bigcat88/pillow_heif/tree/v1.7.0> |
| libheif | 1.23.3 (`78c9746aea226b22885e8d35241353ce669c4ea5`) | LGPLv3 | <https://github.com/strukturag/libheif/tree/v1.23.3> |
| libde265 | 1.1.2 (`d0bcab76380c079358a3156b3e3b37d17c00a078`) | LGPLv3 | <https://github.com/strukturag/libde265/tree/v1.1.2> |
| x265 | 4.2 (`e444744c03978c1fb4e037168967020cf2648427`) | GPLv2 | <https://bitbucket.org/multicoreware/x265_git/src/4.2/> |

Windows wheels additionally contain the MinGW-w64 runtime under
GPL-3.0-with-GCC-exception, MIT, and BSD terms. The appliance release targets
Linux amd64 and arm64 and does not distribute those Windows wheel files.

The authoritative license text and bundled-component notice are installed at:

```text
/install/lib/python3.12/site-packages/pillow_heif-1.7.0.dist-info/licenses/LICENSE.txt
/install/lib/python3.12/site-packages/pillow_heif-1.7.0.dist-info/licenses/LICENSES_bundled.txt
```

The tagged appliance release workflow resolves every ref above, rejects it if
the peeled commit differs, creates a normalized corresponding-source archive,
verifies it with the separately shipped verifier, and attaches the archive,
manifest, checksums, verifier, and this notice to the durable GitHub Release.
Anyone redistributing an appliance image must preserve those files and the
SPDX SBOM, or provide an equivalent durable source distribution; upstream
links alone are not treated as a corresponding-source archive.

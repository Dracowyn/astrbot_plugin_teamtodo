# Vendored front-end libraries

Single files copied unmodified from the npm registry (`npm pack`). No build step, no CDN at runtime.
To upgrade: `npm pack <pkg>@<version>` into an empty directory, extract, copy the one file, update this table.

| File | Package | Version | Source path inside the tarball | License | sha256 of the file |
|---|---|---|---|---|---|
| `vue.esm-browser.prod.js` | `vue` | 3.5.44 | `package/dist/vue.esm-browser.prod.js` | MIT | `37d771e9e91c9c7f78d7db53b3e1f43bb7a48ca5f84d41b4aca95a7cebc8eb5b` |
| `sortable.esm.js` | `sortablejs` | 1.15.7 | `package/modular/sortable.esm.js` | MIT | `a48a4ec931f04c50a1b80c095d0610dc2ec2014d001d4f083405f808fc9be251` |

Tarballs (for reference):

| Tarball | sha256 |
|---|---|
| `vue-3.5.44.tgz` | `33cd651dcae080f76c110bdb035faf9e0bab35f44cd4bd9cf8cf40ae7ed97256` |
| `sortablejs-1.15.7.tgz` | `66cbfc8fa18860783584fe0c346baf6cbf3399da72d23d21053425ba232abed4` |

Notes:

- The Vue build includes the runtime template compiler, which the component modules rely on (`template: \`...\``).
- Both files contain their license header as the first comment and have no `import` statements.

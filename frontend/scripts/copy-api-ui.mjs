import {copyFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
const target=resolve('public/api-ui');
await mkdir(target,{recursive:true});
for(const name of ['swagger-ui.css','swagger-ui-bundle.js','LICENSE']) {
  await copyFile(resolve('node_modules/swagger-ui-dist',name),resolve(target,name));
}

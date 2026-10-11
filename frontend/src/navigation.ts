export function workspacePath(role:string,page:string){return '/'+role+'/'+page;}
export function workspaceRoute(role:string,allowed:string[]){
 const parts=window.location.pathname.split('/').filter(Boolean);
 return parts.length===2&&parts[0]===role&&allowed.includes(parts[1])?parts[1]:null;
}

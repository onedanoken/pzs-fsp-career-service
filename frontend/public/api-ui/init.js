window.addEventListener('load',function(){
 window.ui=SwaggerUIBundle({url:'/api/openapi.json',dom_id:'#swagger-ui',deepLinking:true,displayRequestDuration:true,
   requestInterceptor:async function(request){
     request.credentials='same-origin';
     if(!['GET','HEAD','OPTIONS'].includes(request.method)&&!request.url.includes('/auth/')){
       const response=await fetch('/api/auth/me',{credentials:'same-origin'});
       if(response.ok){const me=await response.json();request.headers['X-CSRF-Token']=me.csrf;}
     }
     return request;
   }
 });
});

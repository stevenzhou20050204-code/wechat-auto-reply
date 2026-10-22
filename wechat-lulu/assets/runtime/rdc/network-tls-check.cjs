'use strict';
// 只握手到公开后端，不带账号、密钥或请求正文。
const tls=require('node:tls');
const {SocksClient}=require('./network-runtime/node_modules/socks');
async function probe(maxVersion) {
 const began=Date.now();let stage='socks',socket;
 try {
  const result=await SocksClient.createConnection({proxy:{host:'127.0.0.1',port:21001,type:5},command:'connect',destination:{host:'olvbkozcufcbptfogatw.supabase.co',port:443},timeout:10000});
  socket=result.socket;stage='tls';
  socket=tls.connect({socket,servername:'olvbkozcufcbptfogatw.supabase.co',rejectUnauthorized:true,ALPNProtocols:['http/1.1'],...(maxVersion?{maxVersion}:{})});
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>socket.destroy(new Error('TLS timeout')),10000);socket.once('secureConnect',()=>{clearTimeout(timer);resolve()});socket.once('error',error=>{clearTimeout(timer);reject(error)})});
  console.log(JSON.stringify({maxVersion:maxVersion||'default',success:true,protocol:socket.getProtocol(),ms:Date.now()-began}));
 } catch(error){console.log(JSON.stringify({maxVersion:maxVersion||'default',success:false,stage,code:error.code||error.message,ms:Date.now()-began}))}
 finally{socket?.destroy()}
}
Promise.all([probe(),probe('TLSv1.2')]);

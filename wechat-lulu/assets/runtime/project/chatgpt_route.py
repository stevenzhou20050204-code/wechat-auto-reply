"""只为本机生成进程建立加密隧道；不解密 TLS，不记录请求正文或凭证。"""
import socket,select,threading,socketserver,time

ALLOWED={'chatgpt.com','chat.openai.com','auth.openai.com','api.openai.com'}
def upstream_authority(authority):
    host,port=authority.rsplit(':',1)
    if host not in ALLOWED or port!='443':raise ValueError('Unsupported tunnel destination')
    return ('chat.openai.com' if host=='chatgpt.com' else host)+':443'

def read_header(sock):
    data=b''
    while b'\r\n\r\n' not in data:
        block=sock.recv(4096)
        if not block:raise ConnectionError('Proxy connection closed')
        data+=block
        if len(data)>16384:raise ValueError('Oversized proxy header')
    return data.split(b'\r\n\r\n',1)

class Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        remote=None;established=False
        try:
            self.request.settimeout(12)
            header,pending=read_header(self.request)
            method,authority,_=header.split(b'\r\n',1)[0].decode('ascii').split(' ',2)
            if method!='CONNECT':raise ValueError('CONNECT only')
            destination=upstream_authority(authority)
            remote=socket.create_connection(('127.0.0.1',21000),timeout=12)
            remote.sendall(('CONNECT '+destination+' HTTP/1.1\r\nHost: '+destination+'\r\n\r\n').encode('ascii'))
            response,extra=read_header(remote)
            if response.split(b'\r\n',1)[0].split(b' ')[1]!=b'200':raise ConnectionError('Upstream refused tunnel')
            self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n'+extra)
            established=True
            if pending:remote.sendall(pending)
            self.request.settimeout(15);remote.settimeout(15)
            self.request.setsockopt(socket.SOL_SOCKET,socket.SO_KEEPALIVE,1)
            remote.setsockopt(socket.SOL_SOCKET,socket.SO_KEEPALIVE,1)
            touched=time.monotonic()
            while True:
                ready,_,_=select.select([self.request,remote],[],[],15)
                if not ready:
                    if time.monotonic()-touched>300:return
                    continue
                for source in ready:
                    data=source.recv(65536)
                    if not data:return
                    (remote if source is self.request else self.request).sendall(data)
                    touched=time.monotonic()
        except (OSError,ValueError,ConnectionError):
            if not established:
                try:self.request.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
                except OSError:pass
        finally:
            if remote:remote.close()

class LocalRoute(socketserver.ThreadingTCPServer):
    allow_reuse_address=False
    daemon_threads=True
    def __init__(self,port=21002):super().__init__(('127.0.0.1',port),Tunnel)
    def start(self):
        self.thread=threading.Thread(target=self.serve_forever,daemon=True,name='wechat-model-tunnel')
        self.thread.start();return self
    def close(self):self.shutdown();self.server_close()

if __name__=='__main__':
    with LocalRoute() as server:server.serve_forever()

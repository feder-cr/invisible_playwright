import socket,json,subprocess,time,os,sys,base64
base=os.getcwd(); prof=base+'/prof'; os.makedirs(prof,exist_ok=True)
exe=base+'/ex3/core/firefox.exe'
open(prof+'/user.js','w').write('user_pref("marionette.port",2828);user_pref("browser.shell.checkDefaultBrowser",false);user_pref("datareporting.policy.dataSubmissionEnabled",false);user_pref("app.update.auto",false);user_pref("app.update.enabled",false);\n')
p=subprocess.Popen([exe,'-headless','-marionette','-remote-allow-system-access','-no-remote','-profile',prof,'about:blank'],env=dict(os.environ,MOZ_REMOTE_ALLOW_SYSTEM_ACCESS='1'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    s=None
    for i in range(60):
        try:
            s=socket.create_connection(('127.0.0.1',2828),timeout=5);break
        except Exception: time.sleep(1)
    f=s.makefile('rwb',buffering=0)
    def rd():
        n=b''
        while True:
            c=s.recv(1)
            if c==b':':break
            n+=c
        n=int(n);d=b''
        while len(d)<n: d+=s.recv(n-len(d))
        return json.loads(d)
    mid=[0]
    def cmd(name,params=None):
        mid[0]+=1
        m=json.dumps([0,mid[0],name,params or {}]).encode()
        s.sendall(str(len(m)).encode()+b':'+m)
        while True:
            r=rd()
            if r[0]==1 and r[1]==mid[0]: return r[2] if r[2] else r[3]
    s.settimeout(60)
    rd()
    cmd('WebDriver:NewSession',{'capabilities':{}})
    h=cmd('WebDriver:GetWindowHandles'); cmd('WebDriver:SwitchToWindow',{'handle':h[0]})
    cmd('WebDriver:Navigate',{'url':'data:text/html,<body><p id=p>text</p>'})
    js="""
    const fonts=['menu','caption','icon','message-box','small-caption','status-bar','system-ui'];
    const colors=['Canvas','CanvasText','LinkText','VisitedText','ActiveText','ButtonFace','ButtonText','ButtonBorder','Field','FieldText','GrayText','Highlight','HighlightText','Mark','MarkText','SelectedItem','SelectedItemText','AccentColor','AccentColorText','ActiveBorder','ActiveCaption','AppWorkspace','Background','ButtonHighlight','ButtonShadow','CaptionText','InactiveBorder','InactiveCaption','InactiveCaptionText','InfoBackground','InfoText','Menu','MenuText','Scrollbar','ThreeDDarkShadow','ThreeDFace','ThreeDHighlight','ThreeDLightShadow','ThreeDShadow','Window','WindowFrame','WindowText'];
    const o={fonts:{},light:{},dark:{},selection:{}};
    for(const f of fonts){const d=document.createElement('div');d.style.font=f==='system-ui'?'16px system-ui':f;document.body.appendChild(d);const c=getComputedStyle(d);o.fonts[f]={family:c.fontFamily,size:c.fontSize,weight:c.fontWeight,style:c.fontStyle,stretch:c.fontStretch};d.remove();}
    for(const sch of ['light','dark']){for(const k of colors){const d=document.createElement('div');d.style.colorScheme=sch;d.style.color=k;d.style.backgroundColor=k;document.body.appendChild(d);o[sch][k]=getComputedStyle(d).color;d.remove();}
      const d=document.createElement('div');d.style.colorScheme=sch;d.textContent='x';document.body.appendChild(d);const c=getComputedStyle(d,'::selection');o.selection[sch]={color:c.color,bg:c.backgroundColor};d.remove();}
    return o;"""
    r=cmd('WebDriver:ExecuteScript',{'script':js,'args':[]})
    json.dump(r['value'] if 'value' in r else r,open(base+'/retail_theme.json','w'),indent=1)
    print(json.dumps(r)[:1500])
finally:
    subprocess.run(['taskkill','/F','/T','/PID',str(p.pid)],capture_output=True)

import requests, urllib.parse, re, json, pathlib, sys, time, subprocess, os, html
from collections import deque

ID = "7435545283435531520"
SHARE = "https://v.douyin.com/6vIVo9HVdGM/"
OUT = pathlib.Path("output")
OUT.mkdir(exist_ok=True)
WEBUA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MOBUA = "Mozilla/5.0 (Linux; Android 12; Pixel 6 Build/SQ3A.220705.003) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
APP_UA = "com.ss.android.ugc.aweme/310600 (Linux; U; Android 13; zh_CN; Pixel 6; Build/TP1A.220624.014; Cronet/TTNetVersion:fb4d219f)"
s = requests.Session()
all_media = []
seen_media = set()
found_metadata = []
seen_item_ids = set()

def add_media(url, context):
    if not isinstance(url,str) or not url.startswith("http"): return
    url = html.unescape(url).replace("\\u0026","&").replace("\\u002F","/")
    if url in seen_media: return
    seen_media.add(url)
    all_media.append((url,context))
    print("MEDIA_CANDIDATE",context, urllib.parse.urlsplit(url).netloc,urllib.parse.urlsplit(url).path[:95], flush=True)

def gather_video(video, context):
    if not isinstance(video,dict): return
    brs = sorted([v for v in (video.get("bit_rate") or []) if isinstance(v,dict)],key=lambda v:v.get("bit_rate") or 0,reverse=True)
    for v in brs + [video]:
        for field in ("play_addr","play_addr_265","download_addr","play_addr_h264","play_addr_lowbr","download_url","url","video_backup"):
            val = v.get(field)
            if isinstance(val,dict):
                for u in val.get("url_list") or []: add_media(u,context+"/"+field)
            elif isinstance(val,list):
                for u in val:
                    if isinstance(u,str): add_media(u,context+"/"+field)
            elif isinstance(val,str): add_media(val,context+"/"+field)

def record_aweme(o, source):
    if not isinstance(o,dict): return
    awid=str(o.get("aweme_id") or o.get("item_id") or o.get("awemeId") or o.get("id") or "")
    if awid != ID: return
    if source in seen_item_ids: return
    seen_item_ids.add(source)
    author = o.get("author") or o.get("author_info") or {}
    name = author.get("nickname") or author.get("name") if isinstance(author,dict) else ""
    desc=o.get("desc") or o.get("title") or ""
    video=o.get("video") or o.get("video_info") or o.get("videoInfo")
    print("TARGET_FOUND",source,awid,str(name)[:100],str(desc)[:200], "has_video",bool(video), flush=True)
    found_metadata.append({"source":source,"id":awid,"author":name,"desc":desc})
    gather_video(video,source)
    for k in ("video_url","play_url","url"):
        if isinstance(o.get(k),str):add_media(o[k],source+"/"+k)

def deep_walk(obj,source):
    q=deque([(obj,0)])
    num=0
    while q and num<25000:
        v,depth=q.popleft();num+=1
        if depth>18:continue
        if isinstance(v,dict):
            record_aweme(v,source)
            if v.get("videoInfoRes") and isinstance(v["videoInfoRes"],dict):
                print("VIDEO_INFO_RES",source, "item_count",len(v["videoInfoRes"].get("item_list") or []),flush=True)
            for z in v.values():
                if isinstance(z,(dict,list)):q.append((z,depth+1))
        elif isinstance(v,list):
            for z in v[:300]:
                if isinstance(z,(dict,list)):q.append((z,depth+1))

def request_url(url, ua=MOBUA, timeout=18, params=None, label=""):
    try:
        r=s.get(url,params=params,headers={"User-Agent":ua,"Referer":"https://www.douyin.com/","Accept":"*/*","Accept-Language":"zh-CN,zh;q=0.9,en;q=0.5"},timeout=(8,timeout),allow_redirects=True)
        print("FETCH",label, r.status_code,len(r.content),r.url[:150],r.headers.get("content-type"),flush=True)
        return r
    except Exception as e:
        print("FETCH_ERROR",label,repr(e)[:240],flush=True)
        return None

def balanced_json(txt,start):
    openings={"{":"}","[":"]"}
    stack=[]
    quoted=False; escaped=False
    for i in range(start,len(txt)):
        c=txt[i]
        if quoted:
            if escaped:escaped=False
            elif c=="\\":escaped=True
            elif c=='"':quoted=False
            continue
        if c=='"':quoted=True
        elif c in openings:stack.append(openings[c])
        elif stack and c==stack[-1]:
            stack.pop()
            if not stack:return txt[start:i+1]
        elif c in "}]":return ""
    return ""

def parse_embedded(r,label):
    if r is None:return
    txt=r.text
    print("PAGE_MARKERS",label,{k:txt.find(k) for k in [ID,"videoInfoRes","_ROUTER_DATA","__UNIVERSAL_DATA_FOR_REHYDRATION__","RENDER_DATA","play_addr","playwm","play_url","verify","item_list","aweme_detail","filter_detail"]},flush=True)
    for marker in ["_ROUTER_DATA","_SSR_DATA","__INIT_PROPS__","__UNIVERSAL_DATA_FOR_REHYDRATION__","RENDER_DATA","__NEXT_DATA__"]:
        i=txt.find(marker)
        if i<0:continue
        substr=txt[i:i+500]
        print("MARKER_CONTEXT",label,marker,substr[:150],flush=True)
        k=i+len(marker)
        poslist=[]
        poslist.extend([j for j in [txt.find("{",k),txt.find("[",k)] if j>=0 and j<k+250])
        if marker=="RENDER_DATA":
            match=re.search(r'id=["\']RENDER_DATA["\'][^>]*>([^<]+)',txt[i-70:i+150000])
            if match:
                for candidate in (urllib.parse.unquote(match.group(1)),html.unescape(match.group(1))):
                    try:
                        obj=json.loads(candidate);deep_walk(obj,label+"/"+marker);break
                    except Exception as e:print("RENDER_PARSE_ERR",str(e)[:130],flush=True)
        if poslist:
            blob=balanced_json(txt,min(poslist))
            if blob:
                try:
                    obj=json.loads(blob)
                    print("EMBED_PARSE_OK",label,marker,"size",len(blob),flush=True)
                    deep_walk(obj,label+"/"+marker)
                except Exception as e:print("EMBED_ERR",label,marker,str(e)[:150],flush=True)
    # Extract just inline JS JSON strings where the selected ID is nearby
    for match in list(re.finditer(ID,txt))[:5]:
        print("ID_CONTEXT",label,txt[max(0,match.start()-180):match.end()+180].replace("\n"," ")[:430],flush=True)

def try_api(label,url,params=None,ua=MOBUA):
    r=request_url(url,ua=ua,label=label,params=params)
    if r is None:return
    try:
        x=r.json()
        print("API_RESULT",label,"root_keys",list(x)[:18] if isinstance(x,dict) else type(x).__name__,
              "status",x.get("status_code") or x.get("code") if isinstance(x,dict) else "",flush=True)
        if isinstance(x,dict):
            print("API_INFO",label,"msg",str(x.get("msg") or x.get("message") or x.get("status_msg") or "")[:240],flush=True)
            deep_walk(x,label)
            d=x.get("data")
            if isinstance(d,dict) and (d.get("url") or d.get("video_url")):
                md=str(d.get("desc") or d.get("title") or "")
                auth=d.get("author") or {}
                print("THIRD_PARTY_RESULT",label,"author",str(auth)[:100],"title",md[:160],flush=True)
                if md and (("雪" in md) or ("认真" in md) or ("关山" in str(auth))):
                    for field in ("url","video_url","video_backup"):
                        val=d.get(field)
                        if isinstance(val,str):add_media(val,label+"/"+field)
                        elif isinstance(val,list):
                            for u in val:
                                if isinstance(u,str):add_media(u,label+"/"+field)
    except Exception as e:
        print("API_NONJSON",label,r.text[:270],str(e)[:60],flush=True)

for label,url,ua in [
    ("iesmobile",f"https://www.iesdouyin.com/share/video/{ID}/",MOBUA),
    ("iesapp",f"https://www.iesdouyin.com/share/video/{ID}/",APP_UA),
    ("mshare",f"https://m.douyin.com/share/video/{ID}/",MOBUA),
    ("douyinshare",f"https://www.douyin.com/share/video/{ID}/",MOBUA),
    ("douyinpc",f"https://www.douyin.com/video/{ID}",WEBUA),
]:
    r=request_url(url,ua,label=label)
    parse_embedded(r,label)

for label,url,params,ua in [
    ("bugpk","https://api.bugpk.com/api/douyin",{"url":SHARE},WEBUA),
    ("bugpkid","https://api.bugpk.com/api/douyin",{"url":f"https://www.douyin.com/video/{ID}"},WEBUA),
    ("aag","https://api.aag.moe/api/dywsyjx",{"url":SHARE},WEBUA),
    ("iteminfo","https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/",{"item_ids":ID},MOBUA),
    ("iteminfo2","https://www.douyin.com/web/api/v2/aweme/iteminfo/",{"item_ids":ID},WEBUA),
    ("aweme-detail-app","https://aweme.snssdk.com/aweme/v1/aweme/detail/",{"aweme_id":ID,"aid":"1128","device_platform":"android"},APP_UA),
    ("aweme-detail-app2","https://api5-normal-c-hl.amemv.com/aweme/v1/aweme/detail/",{"aweme_id":ID,"aid":"1128","device_platform":"android"},APP_UA),
    ("ies-detail","https://www.iesdouyin.com/aweme/v1/web/aweme/detail/",{"aweme_id":ID},MOBUA),
    ("douyin-webdetail","https://www.douyin.com/aweme/v1/web/aweme/detail/",{"device_platform":"webapp","aid":6383,"aweme_id":ID},WEBUA),
]:
    try_api(label,url,params,ua)

print("MATCHED_META",found_metadata,flush=True)
print("TOTAL_MEDIA",len(all_media),flush=True)
for url,source in all_media[:12]:
    try:
        headers={"User-Agent":APP_UA,"Referer":"https://www.iesdouyin.com/","Accept":"*/*"}
        with s.get(url,headers=headers,stream=True,timeout=(9,45)) as r:
            print("MEDIA_GET",source,r.status_code,r.headers.get("content-type"),r.headers.get("Content-Length"),r.url[:140],flush=True)
            r.raise_for_status()
            if "text/html" in r.headers.get("content-type",""):continue
            tmp=OUT/"video.part"
            with tmp.open("wb") as f:
                total=0
                for b in r.iter_content(chunk_size=1024*1024):
                    if b:
                        f.write(b);total+=len(b)
                        if total>100*1024*1024:raise RuntimeError("too large")
        probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration,size:stream=codec_type,codec_name,width,height","-of","json",str(tmp)],capture_output=True,text=True,timeout=20)
        print("FILE_PROBE",probe.returncode,probe.stdout[:500],probe.stderr[:200],flush=True)
        if probe.returncode==0 and '"video"' in probe.stdout:
            (OUT/"video.mp4").unlink(missing_ok=True)
            tmp.rename(OUT/"video.mp4")
            (OUT/"info.json").write_text(json.dumps({"source":source,"id":ID,"metadata":found_metadata,"probe":json.loads(probe.stdout)},ensure_ascii=False,indent=2),encoding="utf8")
            print("SUCCESS_VALIDATED_MP4",str(OUT/"video.mp4"),flush=True)
            sys.exit(0)
    except Exception as e:
        print("MEDIA_ERROR",source,repr(e)[:400],flush=True)
print("NO_VIDEO_FOUND",flush=True)
sys.exit(1)

import requests, pathlib, zipfile, json, time
url="https://v.douyin.com/NJ_DRKMVA_Y/"
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"})
r=s.get("https://api.bugpk.com/api/douyin",params={"url":url},timeout=20)
r.raise_for_status()
p=r.json()
d=p["data"]
assert p["code"]==200 and d["type"]=="image" and "春天是播种的季节" in d["desc"] and d["author"]["name"]=="！"
imgs=d["images"]
assert imgs and len(imgs)<100
out=pathlib.Path("output-fast")
out.mkdir(exist_ok=True)
archive=out/"douyin_spring_images.zip"
with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as z:
 for i,u in enumerate(imgs,1):
  resp=s.get(u,timeout=(8,45),headers={"Referer":"https://www.douyin.com/"})
  resp.raise_for_status()
  content=resp.content
  assert len(content)>5000 and content[:3]==b"\xff\xd8\xff", (i,len(content),content[:16])
  z.writestr(f"image_{i:02d}.jpg",content)
  print("IMAGE_OK",i,len(content),flush=True)
 z.writestr("metadata.json",json.dumps({"author":d["author"]["name"],"title":d["desc"],"id":d["extra"]["aweme_id"],"count":len(imgs)},ensure_ascii=False,indent=2))
print("RESULT",archive,archive.stat().st_size,"images",len(imgs),flush=True)

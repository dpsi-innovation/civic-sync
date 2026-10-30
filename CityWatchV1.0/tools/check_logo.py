import urllib.request, os
for path in ("/", "/app"):
    html = urllib.request.urlopen("http://127.0.0.1:8000" + path).read().decode()
    print(path,
          "| mark:", "shield + sync-orbit" if "shield + sync-orbit" in html else "MISSING",
          "| dasharray:", 'stroke-dasharray="10.4 4.3"' in html,
          "| favicon:", "icon-192.png" in html)
for f in ("web/icons/icon-192.png", "web/icons/icon-512.png"):
    print(f, os.path.getsize(f), "bytes")

#!/usr/bin/env python3
"""Guitar Finder: independent multi-engine guitar listing search. Python 3.9+."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, parse_qsl, urlencode
import json, os, re, threading, time, ipaddress, socket
import requests
from bs4 import BeautifulSoup

PORT = int(os.environ.get('PORT', '8765'))
SEARX_URL = os.environ.get('SEARXNG_URL', '').rstrip('/')
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
SESSION = requests.Session()
HEADERS = {'User-Agent': UA, 'Accept-Language':'en-GB,en;q=0.9'}

SITES = {
 'Reverb':'reverb.com', 'eBay UK':'ebay.co.uk', 'Gumtree':'gumtree.com',
 'Facebook Marketplace':'facebook.com/marketplace', 'Reddit':'reddit.com',
 'theFretBoard':'thefretboard.co.uk', 'Cash Converters':'cashconverters.co.uk',
 'Guitar Village':'guitarvillage.co.uk', 'GuitarGuitar':'guitarguitar.co.uk',
 'Andertons':'andertons.co.uk', 'Peach Guitars':'peachguitars.com',
 'Coda Music':'coda-music.com', 'The Music Locker':'themusiclocker.co.uk',
 'The Guitar Showroom':'theguitarshowroom.co.uk', 'Vintage & Rare':'vintageandrare.com',
 'Treblerock Bristol':'treblerock.com', 'Candy Skull Guitars':'candyskullguitars.com',
 'Intasound':'intasound-music.co.uk', 'Oasis Music':'oasismusic.co.uk',
 'Hobgoblin':'hobgoblin.com', 'Vintage Guitar Boutique':'vintageguitarboutique.com',
 'ATB Guitars':'atbguitars.com', 'Andy Baxter Bass':'andybaxterbass.com',
 'Wunjo Guitars':'wunjoguitars.com', 'Sixty Sixty Sounds':'sixtysixtysounds.com',
 'Guitar Classics':'guitar-classics.com', 'Sound Affects':'soundaffectsmusic.com',
 'GuitarPoint':'guitarpoint.de', 'Gregs Guitars':'gregsguitars.de',
 'Mercatino Musicale':'mercatinomusicale.com', 'Kleinanzeigen':'kleinanzeigen.de',
 'Willhaben':'willhaben.at', 'Leboncoin':'leboncoin.fr', 'Marktplaats':'marktplaats.nl',
 'Wallapop':'wallapop.com', 'Subito':'subito.it', 'Music Store':'musicstore.com',
 'Thomann':'thomann.de', 'Other independent dealers':''
}
COMMUNITY = {'Reddit', 'theFretBoard'}
CORE = ['Reverb','eBay UK','Gumtree','theFretBoard','Reddit','Cash Converters','Vintage & Rare']
DEALERS = [k for k in SITES if k not in CORE and k not in ['Facebook Marketplace','Other independent dealers'] and k not in ['Mercatino Musicale','Kleinanzeigen','Willhaben','Leboncoin','Marktplaats','Wallapop','Subito']]
EU_MARKETS = ['Mercatino Musicale','Kleinanzeigen','Willhaben','Leboncoin','Marktplaats','Wallapop','Subito']

BLOCKED = ('google.com/aclk','googleadservices','doubleclick.net','bing.com/aclick','duckduckgo.com/y.js')
IGNORE = ('/search?', '/search/', '/category/', '/categories/', '/collections/', '/pages/', '/shops/', '/store/', '/stores/', '/guide/', '/blog/', '/articles/', '/help/', '/sell', '/login', '/signin')
PRICE = re.compile(r'(?<!\w)(?:£\s?\d[\d,]*(?:\.\d{2})?|€\s?\d[\d.,]*|\d[\d.,]*\s?(?:GBP|EUR))(?!\w)', re.I)


def real_url(url):
 if not url: return ''
 if url.startswith('//'): url='https:'+url
 if url.startswith('/') and not url.startswith('//'): return ''
 p=urlparse(url)
 qs=dict(parse_qsl(p.query))
 if 'uddg' in qs: url=unquote(qs['uddg']); p=urlparse(url)
 if p.scheme not in ('https','http') or not p.netloc: return ''
 if any(b in url.lower() for b in BLOCKED): return ''
 # strip tracking, but leave useful product variants
 params=[(k,v) for k,v in parse_qsl(p.query) if k.lower() in ('id','item','variant','product','pid')]
 from urllib.parse import urlunparse
 return urlunparse((p.scheme,p.netloc.lower(),p.path.rstrip('/') or '/', '', urlencode(params), ''))


def site_name(url):
 host=urlparse(url).netloc.removeprefix('www.')
 for name, domain in SITES.items():
  if domain and (host==domain.split('/')[0] or host.endswith('.'+domain.split('/')[0])): return name
 return host


def likely_listing(url, title):
 p=urlparse(url); path=p.path.lower(); host=p.netloc.lower()
 if not path or path=='/': return False
 if any(i in path for i in IGNORE): return False
 if any(x in host for x in ('youtube.com','pinterest.','wikipedia.org','instagram.com','tiktok.com','amazon.')): return False
 if 'reddit.com' in host: return '/comments/' in path
 if 'facebook.com' in host: return '/marketplace/item/' in path
 if 'thefretboard.co.uk' in host: return '/discussion/' in path
 if any(x in host for x in ('reverb.com','ebay.','gumtree.','kleinanzeigen.','leboncoin.','subito.','marktplaats.')):
  return any(x in path for x in ('/item/','/itm/','/p/','/ad/','/ads/','/s-ad/','/vi/','/v/','/angebote/','/anzeigen/','/a/','/d/','/listing/')) or len(path.strip('/').split('/'))>=2
 return len(path.strip('/').split('/'))>=1 and len(title)>=8


def engine_ddg(query):
 r=SESSION.get('https://html.duckduckgo.com/html/', params={'q':query,'kl':'uk-en'},headers=HEADERS,timeout=12)
 r.raise_for_status()
 soup=BeautifulSoup(r.text,'html.parser'); out=[]
 for el in soup.select('.result'):
  a=el.select_one('a.result__a')
  if not a: continue
  snippet=el.select_one('.result__snippet')
  out.append((a.get_text(' ',strip=True),a.get('href',''),snippet.get_text(' ',strip=True) if snippet else '', 'DuckDuckGo'))
 return out


def engine_bing(query):
 r=SESSION.get('https://www.bing.com/search',params={'q':query,'cc':'GB','setlang':'en-GB','count':30},headers=HEADERS,timeout=12)
 r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser'); out=[]
 for el in soup.select('li.b_algo'):
  a=el.select_one('h2 a[href]')
  if not a: continue
  snippet=el.select_one('.b_caption p') or el.select_one('p')
  out.append((a.get_text(' ',strip=True),a.get('href',''),snippet.get_text(' ',strip=True) if snippet else '', 'Bing'))
 return out


def engine_searx(query):
 if not SEARX_URL: return []
 r=SESSION.get(SEARX_URL+'/search',params={'q':query,'format':'json','language':'en-GB'},headers=HEADERS,timeout=12)
 r.raise_for_status()
 return [(x.get('title',''),x.get('url',''),x.get('content',''), 'SearXNG') for x in r.json().get('results',[])]


def make_queries(term, region, sites):
 regionword={'UK':'(UK OR Britain OR GBP OR £)','EU':'(Europe OR EUR OR €)','both':'(UK OR Europe OR GBP OR EUR)'}.get(region,'')
 words=f'"{term}" guitar for sale {regionword} -review -youtube -pinterest'
 queries=[('DuckDuckGo',words),('Bing',words)]
 if sites and 'Other independent dealers' not in sites:
  selection=[name for name in sites if name in SITES and SITES[name]]
 else:
  selection=CORE + (EU_MARKETS if region != 'UK' else []) + DEALERS
 # Rotate engines, target small sites individually in batches and include long-tail discovery.
 for i in range(0,len(selection),3):
  batch=selection[i:i+3]
  expr=' OR '.join('site:'+SITES[name] for name in batch)
  queries.append(('DuckDuckGo' if (i//3)%2==0 else 'Bing',f'"{term}" ({expr})'))
 if not sites or 'Other independent dealers' in sites:
  queries += [
   ('DuckDuckGo',f'"{term}" "used" "guitar" "add to cart" UK -reverb -ebay'),
   ('Bing',f'"{term}" "second hand" "guitars" in stock UK -reverb -ebay'),
   ('DuckDuckGo',f'"{term}" vintage guitar shop for sale UK -reverb -ebay'),
  ]
 if not sites or 'Reddit' in sites:
  queries.append(('Bing',f'"{term}" site:reddit.com/r/ "[WTS]" OR "for sale"'))
 if not sites or 'Facebook Marketplace' in sites:
  queries.append(('DuckDuckGo',f'"{term}" site:facebook.com/marketplace/item/'))
 if SEARX_URL:queries.append(('SearXNG',words))
 return queries[:32]

def search(term, region, site_names, min_price, max_price, used_only):
 queries=make_queries(term,region,site_names)
 engines={'DuckDuckGo':engine_ddg,'Bing':engine_bing,'SearXNG':engine_searx}
 combined=[]; errors=[]
 with ThreadPoolExecutor(max_workers=9) as pool:
  futures={pool.submit(engines[e],q):(e,q) for e,q in queries}
  for f in as_completed(futures):
   e,q=futures[f]
   try:combined.extend(f.result())
   except Exception as ex:errors.append(f'{e} unavailable: {type(ex).__name__}')
 found={}
 terms=[x.lower() for x in re.findall(r'[a-z0-9]+',term) if len(x)>1]
 for title,url,snippet,engine in combined:
  url=real_url(url); title=' '.join(title.split()); snippet=' '.join(snippet.split())
  if not url or not likely_listing(url,title):continue
  if site_names and 'Other independent dealers' not in site_names and not any(SITES[x].split('/')[0] in urlparse(url).netloc for x in site_names if x in SITES and SITES[x]):continue
  blob=(title+' '+snippet).lower()
  if terms and sum(t in blob for t in terms)/len(terms)<0.7:continue
  if used_only and ('brand new' in blob or ('new condition' in blob and 'used' not in blob)):continue
  if any(tag in title.lower() for tag in ('sold:', 'sold -', 'sold ', 'withdrawn:', 'wtb:', 'wanted:')) and ('reddit.com' in url or 'thefretboard.co.uk' in url): continue
  match=PRICE.search(title+' '+snippet)
  price=match.group(0) if match else None
  numeric=None
  if price:
   try: numeric=float(re.sub(r'[^0-9.]','',price.replace(',','')))
   except ValueError: pass
  # Only filter by numeric price when denomination corresponds to the filter's GBP assumption.
  if numeric is not None and price and ('£' in price or 'GBP' in price.upper()):
   if min_price is not None and numeric<min_price:continue
   if max_price is not None and numeric>max_price:continue
  key=(urlparse(url).netloc.lower(),urlparse(url).path.rstrip('/').lower())
  if key not in found:
   found[key]={'title':title,'url':url,'snippet':snippet[:320],'site':site_name(url),'price':price,'engines':[engine], 'photo_endpoint':'/api/image?url='+quote_plus(url)}
  elif engine not in found[key]['engines']: found[key]['engines'].append(engine)
 results=list(found.values())
 results.sort(key=lambda x:(-len(x['engines']),-sum(t in x['title'].lower() for t in terms), x['site']))
 return {'results':results[:120], 'total':len(results), 'raw':len(combined), 'warnings':sorted(set(errors)), 'queries':len(queries)}

# Listing-photo previews are fetched only from explicitly configured public sources.
# Strict allowed hosts, no redirect following and no local-network targets.
def listing_photo(url):
 p=urlparse(url)
 host=(p.hostname or '').lower()
 allowed=[d.split('/')[0] for d in SITES.values() if d]
 if p.scheme!='https' or not host or not any(host==d or host.endswith('.'+d) for d in allowed): return None
 # Avoid private hostnames and IPs; the host must be public DNS.
 try:
  addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
  if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):return None
 except (OSError,ValueError): return None
 try:
  resp=requests.get(url,headers=HEADERS,timeout=5,allow_redirects=False,stream=True)
  if resp.status_code!=200 or 'text/html' not in resp.headers.get('Content-Type',''):return None
  blob=resp.raw.read(350000,decode_content=True)
  soup=BeautifulSoup(blob,'html.parser')
  for attrs in ({'property':'og:image'},{'name':'twitter:image'}):
   tag=soup.find('meta',attrs=attrs)
   if tag and tag.get('content'):
    from urllib.parse import urljoin
    img=urljoin(url,tag['content'])
    i=urlparse(img)
    if i.scheme=='https' and i.hostname and i.hostname not in ('localhost',):return img
 except (requests.RequestException,ValueError,TypeError):return None
 return None

IMAGE_CACHE={}
IMAGE_LOCK=threading.Lock()

STATIC = {
 '/icons/guitar-finder.svg': ('icons/guitar-finder.svg','image/svg+xml'),
 '/manifest.webmanifest': ('manifest.webmanifest','application/manifest+json'),
 '/sw.js': ('sw.js','text/javascript; charset=utf-8'),
 '/icons/icon-192.png': ('icons/icon-192.png','image/png'),
 '/icons/icon-512.png': ('icons/icon-512.png','image/png'),
 '/icons/apple-touch-icon.png': ('icons/apple-touch-icon.png','image/png'),
}

class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  path=urlparse(self.path)
  if path.path=='/':
   with open(os.path.join(os.path.dirname(__file__),'index.html'),'rb') as f: data=f.read()
   self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
  if path.path in STATIC:
   filename,mimetype=STATIC[path.path]
   with open(os.path.join(os.path.dirname(__file__),filename),'rb') as f: blob=f.read()
   self.send_response(200)
   self.send_header('Content-Type',mimetype)
   self.send_header('Cache-Control','public, max-age=3600' if filename!='sw.js' else 'no-cache')
   self.send_header('Content-Length',str(len(blob)))
   self.end_headers();self.wfile.write(blob);return
  if path.path=='/health':
   self.json({'status':'ok'});return
  if path.path=='/api/image':
   url=parse_qs(path.query).get('url',[''])[0]
   with IMAGE_LOCK: cache=IMAGE_CACHE.get(url)
   if cache and time.time()-cache[0]<3600: photo=cache[1]
   else:
    photo=listing_photo(url)
    with IMAGE_LOCK:
     if len(IMAGE_CACHE)>1500: IMAGE_CACHE.clear()
     IMAGE_CACHE[url]=(time.time(),photo)
   self.json({'image':photo});return
  if path.path=='/api/search':
   args=parse_qs(path.query)
   def one(k,default=''):return args.get(k,[default])[0]
   term=one('q').strip()[:140]
   if len(term)<2:self.json({'error':'Enter at least two characters'},400);return
   region=one('region','both')
   sites=[x for x in one('sites').split(',') if x in SITES]
   def price(k):
    try:return max(0,float(one(k))) if one(k) else None
    except ValueError:return None
   data=search(term,region,sites,price('min'),price('max'),one('used')=='1')
   self.json(data);return
  self.send_error(404)
 def json(self,value,status=200):
  blob=json.dumps(value,ensure_ascii=False).encode('utf-8')
  self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(blob)));self.end_headers();self.wfile.write(blob)
 def log_message(self,*args):pass

if __name__=='__main__':
 print(f'Guitar Finder: http://localhost:{PORT}');print('Ctrl+C to stop')
 if SEARX_URL:print('SearXNG enabled:',SEARX_URL)
 ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'), PORT),Handler).serve_forever()
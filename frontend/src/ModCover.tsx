import {useEffect, useRef, useState} from 'react';
import {Package} from 'lucide-react';
import {api, type Mod} from './api';

export function useWorkshopCovers(mods: Mod[] | undefined, enabled: boolean) {
  const [covers, setCovers] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const ids = [...new Set(mods?.flatMap(mod => mod.workshop_id && /^[0-9]{7,12}$/.test(mod.workshop_id) ? [mod.workshop_id] : []))];
    async function load(batch: string[], cachedOnly: boolean) {
      const query = new URLSearchParams({ids: batch.join(','), cached_only: String(cachedOnly)});
      try {
        const {covers: next} = await api<{covers: Record<string, string>}>(`/workshop/covers?${query}`, {signal: controller.signal});
        if (!controller.signal.aborted) setCovers(previous => ({...previous, ...next}));
        return next;
      } catch { return {}; }
    }
    void (async () => {
      let missing: string[] = [];
      for (let start = 0; start < ids.length && !controller.signal.aborted; start += 30) {
        const batch = ids.slice(start, start + 30);
        const cached: Record<string, string> = await load(batch, true);
        missing = missing.concat(batch.filter(id => !cached[id]));
      }
      for (let start = 0; start < missing.length && !controller.signal.aborted; start += 30) {
        await load(missing.slice(start, start + 30), false);
      }
    })();
    return () => controller.abort();
  }, [mods, enabled]);
  return covers;
}

export function ModCover({preview, localId, className}: {preview?: string; localId?: string; className: string}) {
  // Remount on source changes so errors from the previous cover do not follow the new one.
  return <Cover key={JSON.stringify([preview, localId])} preview={preview} localId={localId} className={className}/>;
}

function Cover({preview, localId, className}: {preview?: string; localId?: string; className: string}) {
  const container = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [workshopFailed, setWorkshopFailed] = useState(false);
  const [localFailed, setLocalFailed] = useState(false);
  const [image, setImage] = useState<{endpoint: string; url: string}>();
  const workshop = !workshopFailed && preview && /^\/api\/workshop\/cover\?id=[0-9]{1,20}&v=[a-f0-9]{64}$/.test(preview);
  const endpoint = workshop ? preview : localId && !localFailed ? `/api/mods/cover?${new URLSearchParams({id: localId})}` : '';
  useEffect(() => {
    const target = container.current;
    if (!target) return;
    if (typeof IntersectionObserver === 'undefined') {setVisible(true); return;}
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      setVisible(true);
      observer.disconnect();
    }, {rootMargin: '160px'});
    observer.observe(target);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible || !endpoint) return;
    const controller = new AbortController();
    let url = '';
    void fetch(endpoint, {
      headers: {'X-PSMM-Request': '1'}, signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error('Cover unavailable');
      const data = await response.blob();
      if (!controller.signal.aborted) {
        url = URL.createObjectURL(data);
        setImage({endpoint, url});
      }
    }).catch(() => {
      if (controller.signal.aborted) return;
      if (workshop) setWorkshopFailed(true); else setLocalFailed(true);
    });
    return () => {controller.abort(); if (url) URL.revokeObjectURL(url);};
  }, [endpoint, visible, workshop]);
  const src = image?.endpoint === endpoint ? image.url : '';
  return <span ref={container} className={`mod-cover ${className}`} aria-hidden="true">{src ?
    <img src={src} alt="" decoding="async" onError={() => workshop ? setWorkshopFailed(true) : setLocalFailed(true)}/> :
    <Package size={16}/>}</span>;
}

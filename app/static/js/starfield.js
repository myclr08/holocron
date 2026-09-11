// Yildiz alani: sayfanin arkasinda duran tek canvas.
// Uc derinlik katmani, yavas suruklenme (paralaks), birkac parildayan yildiz.
// "Guncelle" calisirken hyperspace kipine gecer: yildizlar merkezden disa uzar.
//
// CPU icin alinan onlemler:
//  - Yildizlar saydamlik kovalarina ayrilir: kare basina 8-10 fillStyle atamasi,
//    ara dizge uretimi yok.
//  - Kare hizi sinirli (sakin 12 fps, hyperspace 30 fps).
//  - Sekme gizliyken dongu tamamen durur, pencere olculeri degisince yeniden kurulur.
//  - devicePixelRatio en fazla 2.
//  - Hareket kapaliyken (ayar ya da isletim sistemi tercihi) tek kare cizilir.

const Starfield = (function () {
  const LAYERS = [
    { count: 120, speed: 0.06, size: 0.7, alpha: 0.45 },
    { count: 66, speed: 0.15, size: 1.05, alpha: 0.7 },
    { count: 34, speed: 0.3, size: 1.5, alpha: 1 },
  ];
  const CALM_FRAME_MS = 1000 / 12;
  const HYPER_FRAME_MS = 1000 / 30;
  const BUCKETS = 8; // saydamlik kademesi sayisi
  const MAX_DPR = 2;
  const TWINKLE_EVERY = 26; // kabaca her 26 yildizdan biri parildar
  const HYPER_IN_MS = 420;
  const HYPER_OUT_MS = 600;
  const STAR_RGB = "232, 241, 255";
  // Canvas saydam degil: katman opak olunca tarayici harmanlama yapmadan kopyalar.
  // Zemin rengi sayfayla ayni olmali (--bg).
  const SKY = "#050810";

  let canvas = null;
  let ctx = null;
  let stars = [];
  let twinklers = [];
  let buckets = [];
  let width = 0;
  let height = 0;
  let dpr = 1;
  let frame = 0;
  let lastTick = 0;
  let enabled = true;
  let motion = true;
  let hyperTarget = 0;
  let hyper = 0;
  let hyperChangedAt = 0;
  let started = false;

  function reducedMotion() {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function animating() {
    return motion && !reducedMotion();
  }

  function seed() {
    stars = [];
    twinklers = [];
    let index = 0;
    LAYERS.forEach((layer, depth) => {
      for (let i = 0; i < layer.count; i += 1) {
        index += 1;
        const star = {
          x: Math.random() * width,
          y: Math.random() * height,
          depth: depth,
          speed: layer.speed,
          size: layer.size * (0.75 + Math.random() * 0.5),
          alpha: layer.alpha * (0.6 + Math.random() * 0.4),
          twinkle: index % TWINKLE_EVERY === 0 ? Math.random() * Math.PI * 2 : 0,
        };
        stars.push(star);
        if (star.twinkle) twinklers.push(star);
      }
    });
  }

  /** Yildizlari saydamliga gore kovalara ayirir: kare basina birkac fillStyle yeter. */
  function bucketize() {
    const map = new Map();
    stars.forEach((star) => {
      if (star.twinkle) return;
      const step = Math.max(1, Math.round(star.alpha * BUCKETS));
      if (!map.has(step)) {
        map.set(step, { style: "rgba(" + STAR_RGB + ", " + (step / BUCKETS).toFixed(3) + ")", items: [] });
      }
      map.get(step).items.push(star);
    });
    buckets = Array.from(map.values());
  }

  function resize() {
    if (!canvas) return;
    dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seed();
    bucketize();
    draw(0);
  }

  function wrap(value, span) {
    const rest = value % span;
    return rest < 0 ? rest + span : rest;
  }

  function drawCalm(now) {
    const phase = now / 900;
    for (let i = 0; i < buckets.length; i += 1) {
      const bucket = buckets[i];
      ctx.fillStyle = bucket.style;
      const items = bucket.items;
      for (let k = 0; k < items.length; k += 1) {
        ctx.fillRect(items[k].x, items[k].y, items[k].size, items[k].size);
      }
    }
    for (let i = 0; i < twinklers.length; i += 1) {
      const star = twinklers[i];
      const alpha = star.alpha * (0.55 + 0.45 * Math.sin(phase + star.twinkle));
      ctx.fillStyle = "rgba(" + STAR_RGB + ", " + alpha.toFixed(3) + ")";
      ctx.fillRect(star.x, star.y, star.size, star.size);
    }
  }

  function drawStreaks() {
    const midX = width / 2;
    const midY = height / 2;
    ctx.strokeStyle = "rgba(" + STAR_RGB + ", .8)";
    for (let i = 0; i < stars.length; i += 1) {
      const star = stars[i];
      const dx = star.x - midX;
      const dy = star.y - midY;
      const distance = Math.sqrt(dx * dx + dy * dy) || 1;
      const stretch = hyper * (10 + distance * 0.22) * (0.5 + star.depth * 0.45);
      ctx.lineWidth = star.size;
      ctx.beginPath();
      ctx.moveTo(star.x, star.y);
      ctx.lineTo(star.x + (dx / distance) * stretch, star.y + (dy / distance) * stretch);
      ctx.stroke();
    }
  }

  function draw(now) {
    if (!ctx) return;
    ctx.fillStyle = SKY;
    ctx.fillRect(0, 0, width, height);
    if (hyper > 0.01) drawStreaks();
    else drawCalm(now);
  }

  function advance(delta) {
    const midX = width / 2;
    const midY = height / 2;
    const streaking = hyper > 0.01;

    for (let i = 0; i < stars.length; i += 1) {
      const star = stars[i];
      if (streaking) {
        const dx = star.x - midX;
        const dy = star.y - midY;
        const distance = Math.sqrt(dx * dx + dy * dy) || 1;
        const push = hyper * star.speed * delta * 0.9 * (1 + distance / 260);
        star.x += (dx / distance) * push;
        star.y += (dy / distance) * push;
      } else {
        // Sakin kip: hafif capraz suruklenme, derin katman daha yavas.
        star.x -= star.speed * delta * 0.055;
        star.y += star.speed * delta * 0.018;
      }
      if (star.x < -8) star.x = width + 8;
      if (star.x > width + 8) star.x = -8;
      if (star.y < -8) star.y = height + 8;
      if (star.y > height + 8) star.y = -8;
    }
  }

  function tick(now) {
    frame = window.requestAnimationFrame(tick);
    const delta = now - lastTick;
    const budget = hyper > 0.01 || hyperTarget > 0 ? HYPER_FRAME_MS : CALM_FRAME_MS;
    if (delta < budget) return;
    lastTick = now;

    if (hyper !== hyperTarget) {
      const span = hyperTarget > hyper ? HYPER_IN_MS : HYPER_OUT_MS;
      const step = (now - hyperChangedAt) / span;
      hyper = hyperTarget > hyper ? Math.min(hyperTarget, step) : Math.max(hyperTarget, 1 - step);
      // Hyperspace'ten cikarken yildizlar dagilmis olur; yeniden serpilir.
      if (hyper === 0) {
        seed();
        bucketize();
      }
    }

    advance(Math.min(delta, 120));
    draw(now);
  }

  function start() {
    if (started || !enabled || !animating()) return;
    started = true;
    lastTick = 0;
    frame = window.requestAnimationFrame(tick);
  }

  function stop() {
    started = false;
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
  }

  function apply() {
    if (!canvas) return;
    canvas.hidden = !enabled;
    if (!enabled) {
      stop();
      return;
    }
    if (animating()) {
      start();
    } else {
      stop();
      hyper = 0;
      hyperTarget = 0;
      draw(0);
    }
  }

  return {
    mount: function (node) {
      canvas = node || document.getElementById("starfield");
      if (!canvas || !canvas.getContext) return;
      ctx = canvas.getContext("2d", { alpha: false });
      resize();
      window.addEventListener("resize", resize);
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) stop();
        else apply();
      });
      apply();
    },
    setEnabled: function (value) {
      enabled = !!value;
      apply();
    },
    setMotion: function (value) {
      motion = !!value;
      apply();
    },
    hyperspace: function (on) {
      if (!animating()) return;
      hyperTarget = on ? 1 : 0;
      hyperChangedAt = performance.now();
      if (enabled) start();
    },
  };
})();

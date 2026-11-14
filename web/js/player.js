// ClipPlayer: plays only [start, end] of a lecture video.
// YouTube lectures use the IFrame Player API; local files use <video>.
// The custom bar shows progress through the clip only, with play/pause and replay.
import { mediaUrl } from "./api.js";
import { fmt, h, icon } from "./ui.js";

let ytReady = null;
function loadYouTube() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  return (ytReady ||= new Promise((resolve, reject) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { prev?.(); resolve(window.YT); };
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    s.onerror = () => { ytReady = null; reject(new Error("YouTube unavailable")); };
    document.head.appendChild(s);
    setTimeout(() => reject(new Error("YouTube timed out")), 12000);
  }));
}

export class ClipPlayer {
  /**
   * @param {HTMLElement} mount
   * @param {{lecture: object, start: number, end: number, autoplay?: boolean, onEnd?: Function}} opts
   */
  constructor(mount, { lecture, start, end, autoplay = true, onEnd }) {
    Object.assign(this, { lecture, start, end, autoplay, onEnd });
    this.duration = Math.max(1, end - start);
    this.playing = false;
    this.ended = false;

    this.frame = h("div", { class: "player-frame" });
    this.shield = h("button", { class: "player-shield", "aria-label": "Play or pause", onclick: () => this.toggle() });
    this.bigPlay = h("div", { class: "player-bigplay" }, icon("play"));
    this.shield.append(this.bigPlay);
    this.playBtn = h("button", { class: "pbtn", "aria-label": "Play", onclick: () => this.toggle() }, icon("play"));
    this.fill = h("div", { class: "ptrack-fill" });
    this.knob = h("div", { class: "ptrack-knob" });
    this.track = h("div", { class: "ptrack", role: "slider", "aria-label": "Clip progress", tabindex: 0 },
      h("div", { class: "ptrack-bg" }, this.fill), this.knob);
    this.track.addEventListener("click", (e) => {
      const r = this.track.getBoundingClientRect();
      this.seek(this.start + ((e.clientX - r.left) / r.width) * this.duration);
    });
    this.time = h("span", { class: "ptime" }, `00:00 / ${fmt(this.duration)}`);
    this.replayBtn = h("button", { class: "btn btn-ghost btn-sm", onclick: () => this.replay() }, icon("replay"), "Replay clip");
    this.root = h("div", { class: "player" },
      h("div", { class: "player-stage" }, this.frame, this.shield),
      h("div", { class: "player-bar" }, this.playBtn, this.time, this.track, this.replayBtn));
    mount.replaceChildren(this.root);
    this.init();
  }

  async init() {
    if (this.lecture.video_type === "youtube" && this.lecture.youtube_id) {
      try { await this.initYouTube(); } catch (e) { this.fail(e); }
    } else {
      this.initFile();
    }
  }

  initYouTube() {
    return loadYouTube().then((YT) => new Promise((resolve) => {
      const div = h("div");
      this.frame.append(div);
      this.yt = new YT.Player(div, {
        videoId: this.lecture.youtube_id,
        width: "100%", height: "100%",
        playerVars: {
          start: Math.floor(this.start), autoplay: this.autoplay ? 1 : 0, controls: 0, disablekb: 1,
          rel: 0, modestbranding: 1, playsinline: 1, iv_load_policy: 3, fs: 0, hl: "en", cc_lang_pref: "en",
        },
        events: {
          onReady: () => {
            this.ready = true;
            if (this.autoplay) this.play();
            resolve();
          },
          onStateChange: (e) => this.setPlaying(e.data === YT.PlayerState.PLAYING),
        },
      });
      this.tick = setInterval(() => this.poll(), 200);
    }));
  }

  initFile() {
    this.video = h("video", { src: mediaUrl(this.lecture.video_url), preload: "metadata", playsinline: "" });
    this.frame.append(this.video);
    this.video.addEventListener("loadedmetadata", () => {
      this.video.currentTime = this.start;
      this.ready = true;
      if (this.autoplay) this.play();
    });
    this.video.addEventListener("timeupdate", () => this.poll());
    // timeupdate fires only ~4x/s; check every frame while playing so the clip stops on time.
    const frame = () => { if (!this.root.isConnected) return; if (this.playing) this.poll(); requestAnimationFrame(frame); };
    requestAnimationFrame(frame);
    this.video.addEventListener("play", () => this.setPlaying(true));
    this.video.addEventListener("pause", () => this.setPlaying(false));
    this.video.addEventListener("error", () => this.fail(new Error("Video file could not be loaded")));
  }

  fail(err) {
    console.warn(err);
    const href = this.lecture.youtube_id
      ? `https://www.youtube.com/watch?v=${this.lecture.youtube_id}&t=${Math.floor(this.start)}s`
      : mediaUrl(this.lecture.video_url);
    this.frame.replaceChildren(h("div", { class: "player-error" },
      h("p", {}, "The video can't load here (offline?)."),
      h("a", { class: "btn btn-ghost btn-sm", href, target: "_blank", rel: "noopener" }, `Open at ${fmt(this.start)}`)));
    this.shield.remove();
  }

  current() {
    if (this.yt?.getCurrentTime) return this.yt.getCurrentTime();
    if (this.video) return this.video.currentTime;
    return this.start;
  }

  poll() {
    if (!this.ready) return;
    const t = this.current();
    if (this.playing && !this.ended && t >= this.end - 0.05) {
      this.pause();
      this.ended = true;
      this.render(this.end);
      this.root.classList.add("is-ended");
      this.onEnd?.();
      return;
    }
    this.render(t);
  }

  render(t) {
    const p = Math.min(1, Math.max(0, (t - this.start) / this.duration));
    this.fill.style.width = `${p * 100}%`;
    this.knob.style.left = `${p * 100}%`;
    this.time.textContent = `${fmt(p * this.duration)} / ${fmt(this.duration)}`;
  }

  setPlaying(on) {
    this.playing = on;
    this.root.classList.toggle("is-playing", on);
    this.playBtn.replaceChildren(icon(on ? "pause" : "play"));
    this.playBtn.setAttribute("aria-label", on ? "Pause" : "Play");
  }

  play() {
    if (this.ended || this.current() >= this.end - 0.3) this.seek(this.start);
    this.ended = false;
    this.root.classList.remove("is-ended");
    this.yt?.playVideo?.();
    this.video?.play().catch(() => {});
  }
  pause() { this.yt?.pauseVideo?.(); this.video?.pause(); }
  toggle() { this.playing ? this.pause() : this.play(); }
  seek(t) {
    t = Math.min(this.end - 0.2, Math.max(this.start, t));
    this.yt?.seekTo?.(t, true);
    if (this.video) this.video.currentTime = t;
    this.render(t);
  }
  replay() { this.ended = false; this.seek(this.start); this.play(); }

  destroy() {
    clearInterval(this.tick);
    try { this.yt?.destroy(); } catch {}
    this.video?.pause();
    this.root.remove();
  }
}

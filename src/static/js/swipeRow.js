// Swipeable watch-list rows (phones).
// Swipe left past the threshold: mark the next episode as watched.
// Swipe right: reveal the "Pause" and "Drop" actions.
document.addEventListener("alpine:init", () => {
  Alpine.data("swipeRow", ({ canWatch = true } = {}) => ({
    dx: 0,
    startX: 0,
    startY: 0,
    tracking: false,
    locked: null, // "x" once the gesture is horizontal, "y" if it is a scroll
    open: false,
    animating: false,
    canWatch,

    get actionsWidth() {
      return this.$refs.actions ? this.$refs.actions.offsetWidth : 160;
    },

    get watchThreshold() {
      return Math.min(120, this.$el.offsetWidth * 0.3);
    },

    get frontStyle() {
      const transition = this.tracking ? "none" : "transform 180ms ease-out";
      return `transform: translateX(${this.dx}px); transition: ${transition};`;
    },

    start(event) {
      if (event.touches.length !== 1) return;
      this.startX = event.touches[0].clientX - (this.open ? this.actionsWidth : 0);
      this.startY = event.touches[0].clientY;
      this.tracking = true;
      this.locked = null;
      window.dispatchEvent(new CustomEvent("swipe-row-start", { detail: this.$el }));
    },

    move(event) {
      if (!this.tracking) return;
      const x = event.touches[0].clientX - this.startX;
      const y = event.touches[0].clientY - this.startY;

      if (this.locked === null && (Math.abs(x) > 8 || Math.abs(y) > 8)) {
        this.locked = Math.abs(x) > Math.abs(y) ? "x" : "y";
      }
      if (this.locked !== "x") return;

      event.preventDefault();
      const min = this.canWatch ? -this.$el.offsetWidth * 0.6 : -24;
      const max = this.actionsWidth + 24;
      this.dx = Math.max(min, Math.min(max, x));
    },

    end() {
      if (!this.tracking) return;
      this.tracking = false;
      if (this.locked !== "x") {
        if (!this.open) this.dx = 0;
        return;
      }

      if (this.canWatch && this.dx < -this.watchThreshold) {
        this.dx = -this.$el.offsetWidth;
        this.open = false;
        if (navigator.vibrate) navigator.vibrate(10);
        this.$refs.watch.click();
      } else if (this.dx > this.actionsWidth / 2) {
        this.dx = this.actionsWidth;
        this.open = true;
      } else {
        this.close();
      }
    },

    close() {
      this.dx = 0;
      this.open = false;
    },

    closeIfOther(event) {
      if (event.detail !== this.$el && this.open) this.close();
    },
  }));
});

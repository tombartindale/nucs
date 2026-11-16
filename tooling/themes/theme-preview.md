---
marp: true
theme: default
lang: en
title: Theme preview
style: 'section { font-family: "Azo Sans", "Helvetica Neue", Helvetica, Arial, sans-serif; --bcn-safe-bottom: 216px; --bcn-safe-right: 960px; --bcn-font-size: 44px; } section h1, section h2, section h3 { font-weight: 900; }'
---

<!--
Theme preview deck. Run tooling/themes/theme-preview.sh, then edit
tooling/themes/default/default.css and the browser updates as you save.

The front matter above copies what bcn render injects from theme.toml
(tooling/bcn/commands/render.py, marp_source):
  --bcn-safe-bottom  safe_area.bottom: 216px kept clear for subtitles
  --bcn-safe-right   1920 x (1 - safe_area.right): 960px kept clear for a presenter
                     beside the slides (side_by_side). bcn render also renders every
                     deck full-width: set it to 0px to see that pass.
  --bcn-font-size    font_size.en (44px; Mandarin is 50px, see the last slides)
  font-family        fonts.en; the default theme loads no Azo Sans files, so it
                     shows only if Azo Sans is installed on this Mac
  font-weight        title_weight (900)

To preview northumbria instead, set `theme: custom`.

Preview with tooling/themes/theme-preview.sh (or tooling/themes/theme-preview.sh northumbria): it
serves this deck with the pinned Marp bcn render uses, live-reloading as you save.
The VS Code Marp plugin can't show the default theme: it won't let a custom theme
replace its own built-in "default".

This is close to, not the same as, a real render: the plugin has its own Marp
version, and bcn render also strips the Say blocks and measures overflow. Check
the result with `bcn render <topic> --force` before relying on it.
-->

<!-- Guides: tints the reserved areas (subtitles at the bottom, the presenter on the
     right). Content must stay out of them. Delete this block to hide them. -->
<style>
section { position: relative; }
/* ::before, not ::after: Marp keeps ::after for page numbers and hides it without them. */
section::before {
  content: ' ';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(to top, rgba(255, 80, 80, .22) var(--bcn-safe-bottom, 216px), transparent 0),
    linear-gradient(to left, rgba(80, 160, 255, .18) var(--bcn-safe-right, 0px), transparent 0);
}
</style>

# A model learns the flaws in its data

- What past data really records

---

## A slide title

- A short first point
- A second point with **bold** and _emphasis_
- A third point

---

## The longest title a topic is likely to have, running onto two lines

- A long bullet that wraps over several lines, to see how the line height and spacing behave when a point runs on
- Another long bullet, because two of these together are where content starts pushing into the subtitle area
- A third bullet
- A fourth bullet
- A fifth bullet, which is probably one too many

---

## Numbered steps

1. Collect the past decisions
2. Train the model on them
3. Check what it learned
   - A nested point
   - Another nested point

---

## An image

![Funnel](../../example/KV7015/U01/T01/assets/funnel.png)

---

## A table

| Meant to learn    | Actually learned     |
| ----------------- | -------------------- |
| Who would be good | Who was hired before |
| Merit             | Past decisions       |
| Outcomes          | Judgements           |

---

## Text and code

A short paragraph of plain text, as a topic might use for a definition or a quotation rather than bullets.

Inline code: `model.fit(data)`

---

### A third-level heading

- Used less often, but the theme sizes it

---

<!-- Mandarin: bcn render sets 50px and these line-breaking rules for zh decks. -->
<style scoped>
section { --bcn-font-size: 50px; font-family: "Azo Sans", "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", sans-serif; word-break: normal; line-break: strict; }
</style>

## 模型会学习数据中的缺陷

- 过去的数据真正记录了什么
- 数据记录的是人们的决定,而不是事实本身
- 一个较长的要点,用来检查中文在换行时的效果和行距是否合适,以及是否会进入字幕区域

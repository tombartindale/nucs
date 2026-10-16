// Screenshot a title card (bcn bumpers, compose's logo/name-tag/slide-text overlays) with
// the pinned Chrome.
//
// The page may hold one #title element inside a #box with a max-height. The title's
// font size steps down until it fits the box, so a long title wraps and shrinks
// rather than being clipped.
//
// usage (single): node card.mjs <in.html> <out.png> <width> <height> <maxFontPx> <minFontPx> [transparent]
// stdout: {"font_size": N, "fits": true|false}
//
// usage (batch): node card.mjs --batch <manifest.json>
//   manifest.json: [{"in": "...", "out": "...", "w": N, "h": N, "maxF": N, "minF": N, "mode": "transparent"}, ...]
//   One Chrome launch for the whole manifest (a new page per item, closed after use) instead
//   of one per item — for compose's full_bleed_text, which needs one card per slide: a topic
//   with a dozen-plus slides was a dozen-plus cold Chrome launches in a single compose run,
//   the only place in bcn that scaled Chrome usage with slide count rather than a small fixed
//   number of launches per command.
// stdout: one JSON result per line, same shape as single mode, in manifest order.
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import puppeteer from 'puppeteer-core'

const argv = process.argv.slice(2)
const isBatch = argv[0] === '--batch'
const jobs = isBatch
  ? JSON.parse(readFileSync(argv[1], 'utf8'))
  : [(([inHtml, outPng, w, h, maxF, minF, mode]) => ({
      in: inHtml, out: outPng, w: Number(w), h: Number(h), maxF: Number(maxF), minF: Number(minF), mode,
    }))(argv)]

async function shoot(browser, job) {
  const page = await browser.newPage()
  try {
    await page.setViewport({ width: job.w, height: job.h, deviceScaleFactor: 1 })
    await page.goto(pathToFileURL(job.in).href, { waitUntil: 'load', timeout: 60000 })
    const fit = await page.evaluate(async (maxF, minF) => {
      await document.fonts.ready
      await Promise.all([...document.images].map((i) => i.decode().catch(() => {})))
      const title = document.getElementById('title')
      const box = document.getElementById('box')
      if (!title || !box) return { font_size: null, fits: true } // a card with no title, e.g. the logo-only outro
      let size = maxF
      const maxH = parseFloat(getComputedStyle(box).maxHeight)
      const fits = () => title.scrollHeight <= maxH + 1 && title.scrollWidth <= box.clientWidth + 1
      for (; size > minF; size -= 2) {
        title.style.fontSize = `${size}px`
        if (fits()) break
      }
      title.style.fontSize = `${size}px`
      return { font_size: size, fits: fits() }
    }, job.maxF, job.minF)
    // 'transparent': keep the alpha channel, so the card can be laid over a background video.
    await page.screenshot({ path: job.out, type: 'png', omitBackground: job.mode === 'transparent',
      clip: { x: 0, y: 0, width: job.w, height: job.h } })
    return fit
  } finally {
    await page.close()
  }
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH,
  headless: true,
  // --no-sandbox: this runs as root in the Docker image, where Chrome's sandbox needs a
  // non-root user it doesn't have.
  args: ['--allow-file-access-from-files', '--disable-gpu', '--font-render-hinting=none', '--no-sandbox', '--disable-setuid-sandbox'],
})
try {
  for (const job of jobs) {
    const fit = await shoot(browser, job)
    process.stdout.write(JSON.stringify(fit) + '\n')
  }
} finally {
  await browser.close()
}

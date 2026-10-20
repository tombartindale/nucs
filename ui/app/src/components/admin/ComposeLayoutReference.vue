<script setup lang="ts">
// Static reference for [compose.layouts.<name>] fields in programme.toml, next to the raw
// editor above. Purely documentation (no API call) -- kept in sync with tooling/bcn/config.py's
// DEFAULTS["compose"] by hand when that shape changes.
interface Field {
  name: string;
  appliesTo: string;
  meaning: string;
  default: string;
}

const arrangements = [
  { name: 'side_by_side', desc: "16:9. Presenter fills the theme's safe_area.right column; slides fill the rest." },
  { name: 'inset', desc: '16:9. Slides fill the frame; presenter is a small box over them.' },
  { name: 'stacked', desc: 'Vertical (portrait). Frame splits into a presenter band and a slide band.' },
  { name: 'full_bleed_inset', desc: "Vertical. inset's mirror image: presenter fills the frame, slide is the small box." },
  { name: 'full_bleed_text', desc: "Vertical. full_bleed_inset's box replaced by the slide's own heading/bullets text, centred over the presenter with no slide image." },
];

const fields: Field[] = [
  { name: 'arrangement', appliesTo: 'every layout', meaning: 'One of the five arrangements below. Fixed per layout, not really a tunable value.', default: '—' },
  { name: 'width / height', appliesTo: 'every layout', meaning: 'The output frame size in pixels.', default: '1920×1080 (widescreen); 1080×1920 (vertical presets)' },
  { name: 'inset_scale', appliesTo: 'inset, full_bleed_inset', meaning: "Width of the inset box, as a fraction of the frame's own width.", default: '0.3 (0.38 for vertical_inset)' },
  { name: 'inset_position', appliesTo: 'inset, full_bleed_inset', meaning: 'Corner the inset box sits in, e.g. "top-right".', default: 'top-right' },
  { name: 'inset_margin', appliesTo: 'inset, full_bleed_inset', meaning: 'Gap between the inset box and the frame edge, in pixels.', default: '16 (24 for vertical_inset)' },
  { name: 'split_ratio', appliesTo: 'stacked', meaning: "The presenter band's share of the frame height; the slide band gets the rest.", default: '0.5 (0.45 for vertical_stacked)' },
  { name: 'name_tag_width_scale', appliesTo: 'every arrangement except side_by_side', meaning: "Maximum width of the speaker name tag, as a fraction of the frame's width (side_by_side instead fills the theme's safe_area.right column).", default: '0.8' },
  { name: 'name_tag_font_scale', appliesTo: 'every arrangement, including side_by_side', meaning: "The name's font size, as a fraction of the tag box's own width.", default: '0.05' },
  { name: 'name_tag_margin', appliesTo: 'every arrangement except side_by_side', meaning: "Clear space kept around the tag box, as a fraction of the frame's height.", default: '0.03' },
  { name: 'text_band_height', appliesTo: 'full_bleed_text', meaning: "Height of the slide-text band, as a fraction of the frame's height.", default: '0.30' },
  { name: 'text_band_center', appliesTo: 'full_bleed_text', meaning: "Vertical centre of the slide-text band, as a fraction of the frame's height (nudged up automatically if it would dip into the subtitle safe area).", default: '0.5 (middle of frame)' },
  { name: 'text_side_margin', appliesTo: 'full_bleed_text', meaning: "Clear space kept on each side of the slide text, as a fraction of the frame's width.", default: '0.06' },
  { name: 'text_heading_scale', appliesTo: 'full_bleed_text', meaning: 'Slide heading font size, in em relative to the base slide-text font.', default: '1.3' },
  { name: 'text_body_scale', appliesTo: 'full_bleed_text', meaning: 'Body paragraph/list font size, in em relative to the base slide-text font.', default: '0.85' },
  { name: 'text_font_max_scale', appliesTo: 'full_bleed_text', meaning: "Upper bound on the slide-text auto-fit font size, as a fraction of the theme's own content font size.", default: '0.6' },
  { name: 'text_font_min_scale', appliesTo: 'full_bleed_text', meaning: "Lower bound the auto-fit will shrink the slide text to before giving up.", default: '0.3' },
  { name: 'text_color', appliesTo: 'full_bleed_text', meaning: 'CSS color of the slide text.', default: '#ffffff' },
];
</script>

<template>
  <q-card flat bordered>
    <q-expansion-item dense icon="menu_book" label="compose.layouts reference" default-opened>
      <q-card-section class="text-caption text-grey-7 q-pt-none">
        Each entry under <span class="text-mono">[compose.layouts.&lt;name&gt;]</span> is one output variant. The four shipped
        presets are <span class="text-mono">widescreen</span>, <span class="text-mono">vertical_stacked</span>,
        <span class="text-mono">vertical_inset</span> and <span class="text-mono">vertical_text</span> — override any field
        of these in programme.toml, or add fields to a new preset name only where a matching default already exists in bcn's
        own config (new preset names need a code change). <span class="text-mono">[compose].default_layouts</span> lists which
        preset(s) build when nothing is explicitly selected. Every preset carries every field below regardless of whether its
        own arrangement uses it.
      </q-card-section>
      <q-card-section class="q-pt-none">
        <div class="text-caption text-weight-medium q-mb-xs">Arrangements</div>
        <q-markup-table flat dense wrap-cells class="q-mb-md">
          <thead><tr><th class="text-left">arrangement</th><th class="text-left">What it does</th></tr></thead>
          <tbody>
            <tr v-for="a in arrangements" :key="a.name">
              <td class="text-mono">{{ a.name }}</td>
              <td>{{ a.desc }}</td>
            </tr>
          </tbody>
        </q-markup-table>
        <div class="text-caption text-weight-medium q-mb-xs">Fields</div>
        <q-markup-table flat dense wrap-cells>
          <thead>
            <tr>
              <th class="text-left">field</th>
              <th class="text-left">applies to</th>
              <th class="text-left">meaning</th>
              <th class="text-left">default</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in fields" :key="f.name">
              <td class="text-mono">{{ f.name }}</td>
              <td class="text-caption">{{ f.appliesTo }}</td>
              <td>{{ f.meaning }}</td>
              <td class="text-caption text-mono">{{ f.default }}</td>
            </tr>
          </tbody>
        </q-markup-table>
      </q-card-section>
    </q-expansion-item>
  </q-card>
</template>

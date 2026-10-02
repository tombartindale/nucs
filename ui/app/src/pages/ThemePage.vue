<script setup lang="ts">
// Drop in a whole custom theme (CSS, fonts, bumper video/logos) as one self-contained zip,
// edited offline with the author's own tooling. There's no form editing of theme.toml here —
// bcn's own load_theme() validates whatever lands in themes/custom/, and any problem shows up
// as a normal diagnostic the next time a job (render, bumpers) actually loads the theme.
import { ref, shallowRef } from 'vue';
import type { ThemeStatus } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import { useBeacon } from '@/stores/beacon';

const beacon = useBeacon();
const status = shallowRef<ThemeStatus | null>(null);
const busy = ref(false);
const over = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

async function refresh() {
  try { status.value = await api<ThemeStatus>('/api/theme'); } catch (e) { beacon.toast((e as Error).message, true); }
}
void refresh();

async function upload(file: File | undefined) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.zip')) { beacon.toast('Upload a .zip file.', true); return; }
  busy.value = true;
  try {
    await api('/api/theme/upload', { method: 'POST', raw: await file.arrayBuffer() });
    beacon.toast('Theme uploaded.');
    await refresh();
  } catch (e) { beacon.toast((e as Error).message, true); } finally { busy.value = false; }
}
function onDrop(e: DragEvent) { over.value = false; void upload(e.dataTransfer?.files[0]); }

async function setActive(active: boolean) {
  busy.value = true;
  try {
    await api('/api/theme/activate', { method: 'POST', body: { active } });
    beacon.toast(active ? 'Custom theme activated.' : 'Reverted to the default theme.');
    await refresh();
  } catch (e) { beacon.toast((e as Error).message, true); } finally { busy.value = false; }
}
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Theme" sub="Upload a self-contained theme — CSS, fonts, and the bumper video/logos — as a single zip, edited offline. Every path the theme's own theme.toml refers to must stay inside the zip; nothing outside it is read." />
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Upload</q-card-section>
          <q-card-section>
            <div :class="['dropzone q-pa-lg text-center rounded-borders cursor-pointer', { 'bg-blue-1 text-black': over }]"
              style="border: 2px dashed var(--line)" @click="fileInput?.click()" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop">
              <q-icon name="upload_file" size="md" class="q-mb-sm" /><br>
              Drop a theme .zip here, or click to choose.
              <input ref="fileInput" type="file" accept=".zip" class="hidden" @change="upload(($event.target as HTMLInputElement).files?.[0])">
            </div>
          </q-card-section>
          <q-card-section class="text-caption text-grey-7">Replaces the current custom theme wholesale. The zip's top level should be the theme's own files (theme.toml, its CSS, fonts, images, video).</q-card-section>
        </q-card>
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Activate</q-card-section>
          <q-card-section class="row items-center gap-md">
            <q-toggle :model-value="status?.active ?? false" :disable="busy || !status?.uploaded" label="Use the custom theme" @update:model-value="setActive" />
          </q-card-section>
          <q-card-section class="text-caption text-grey-7">
            {{ status?.uploaded ? '' : 'Upload a theme before activating it. ' }}
            Switches programme.toml's theme setting. Run a render or bumpers job afterward to see it take effect — any problem with the theme's files (a missing font, a bad logo path) shows up there as a normal diagnostic.
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Current custom theme</q-card-section>
          <q-card-section v-if="!status" class="text-grey-7">Checking…</q-card-section>
          <template v-else-if="status.uploaded">
            <q-list separator>
              <q-item v-for="name in status.files" :key="name">
                <q-item-section class="text-mono">{{ name }}</q-item-section>
              </q-item>
            </q-list>
            <q-card-section v-if="!status.files.length" class="text-grey-7">Empty.</q-card-section>
          </template>
          <q-card-section v-else class="text-grey-7">No custom theme uploaded yet. The bundled default theme is in use.</q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>

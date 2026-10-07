<script setup lang="ts">
// Drop in a whole custom theme (CSS, fonts, bumper video/logos) as one self-contained zip,
// edited offline with the author's own tooling. There's no form editing of theme.toml here —
// bcn's own load_theme() validates whatever lands in themes/custom/, and any problem shows up
// as a normal diagnostic the next time a job (render, bumpers) actually loads the theme.
import { ref, shallowRef } from 'vue';
import type { ThemeStatus } from '@beacon/shared';
import { api } from '@/api';
import { useBeacon } from '@/stores/beacon';

// changed: an upload or a switch of theme, so the active-theme check beside this can re-run.
const emit = defineEmits<{ changed: [] }>();
const beacon = useBeacon();
const status = shallowRef<ThemeStatus | null>(null);
const busy = ref(false);
// Bytes sent so far, as a fraction; null once the upload has finished and the server is unpacking it.
const sent = ref<number | null>(null);
const over = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

async function refresh() {
  try { status.value = await api<ThemeStatus>('/api/theme'); } catch (e) { beacon.toast((e as Error).message, true); }
}
void refresh();

// fetch() cannot report upload progress, so the zip is sent with XMLHttpRequest, which can.
function sendZip(file: File): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/theme/upload');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) sent.value = e.loaded / e.total; };
    xhr.upload.onload = () => { sent.value = null; };
    xhr.onload = () => {
      if (xhr.status === 401) { window.location.href = '/login.html'; return; }
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      let message = `${xhr.status} ${xhr.statusText}`;
      try { message = JSON.parse(xhr.responseText).error || message; } catch { /* not JSON: keep the status text */ }
      reject(new Error(message));
    };
    xhr.onerror = () => reject(new Error('The upload failed: the connection was lost.'));
    xhr.send(file);
  });
}

async function upload(file: File | undefined) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.zip')) { beacon.toast('Upload a .zip file.', true); return; }
  busy.value = true;
  sent.value = 0;
  try {
    await sendZip(file);
    beacon.toast('Theme uploaded.');
    await refresh();
    emit('changed');
  } catch (e) { beacon.toast((e as Error).message, true); } finally { busy.value = false; sent.value = null; }
}
function onDrop(e: DragEvent) { over.value = false; void upload(e.dataTransfer?.files[0]); }

async function setActive(active: boolean) {
  busy.value = true;
  try {
    await api('/api/theme/activate', { method: 'POST', body: { active } });
    beacon.toast(active ? 'Custom theme activated.' : 'Reverted to the default theme.');
    await refresh();
    emit('changed');
  } catch (e) { beacon.toast((e as Error).message, true); } finally { busy.value = false; }
}
</script>

<template>
  <div>
    <div class="text-caption text-grey-7 q-mb-md">
      Upload a self-contained theme (CSS, fonts, and the bumper video and logos) as a single zip, edited offline.
      Every path the theme's own theme.toml refers to must stay inside the zip; nothing outside it is read.
    </div>
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6">
        <q-card flat bordered class="full-height">
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Upload</q-card-section>
          <q-card-section>
            <div :class="['dropzone q-pa-lg text-center rounded-borders cursor-pointer', { 'bg-blue-1 text-black': over }]"
              style="border: 2px dashed var(--line)" @click="fileInput?.click()" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop">
              <q-icon name="upload_file" size="md" class="q-mb-sm" /><br>
              Drop a theme .zip here, or click to choose.
              <input ref="fileInput" type="file" accept=".zip" class="hidden" @change="upload(($event.target as HTMLInputElement).files?.[0])">
            </div>
            <div v-if="busy" class="q-mt-md">
              <template v-if="sent !== null">
                <q-linear-progress :value="sent" size="8px" rounded color="primary" />
                <div class="text-caption text-grey-7 q-mt-xs">Uploading… {{ Math.round(sent * 100) }}%</div>
              </template>
              <div v-else class="row items-center gap-sm text-grey-7">
                <q-spinner color="primary" size="20px" />
                <span>Unpacking the theme on the server…</span>
              </div>
            </div>
          </q-card-section>
          <q-card-section class="text-caption text-grey-7">Replaces the current custom theme wholesale. The zip's top level should be the theme's own files (theme.toml, its CSS, fonts, images, video).</q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6">
        <q-card flat bordered class="full-height">
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Activate</q-card-section>
          <q-card-section class="row items-center gap-md">
            <q-toggle :model-value="status?.active ?? false" :disable="busy || !status?.uploaded" label="Use the custom theme" @update:model-value="setActive" />
          </q-card-section>
          <q-card-section class="text-caption text-grey-7">
            {{ status?.uploaded ? '' : 'No custom theme uploaded yet, so the bundled default is in use. ' }}
            Switches programme.toml's theme setting. Run a render or bumpers job afterward to see it take effect.
            What is actually live, and any problem with its files, is shown under Active theme below.
          </q-card-section>
        </q-card>
      </div>
    </div>
  </div>
</template>

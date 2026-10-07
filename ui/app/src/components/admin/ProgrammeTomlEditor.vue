<script setup lang="ts">
// programme.toml, edited as plain text. The server has bcn check it before saving and
// refuses a change made on top of an out-of-date copy (the sha256 it was loaded with).
import { computed, onMounted, ref, shallowRef } from 'vue';
import { api } from '@/api';
import { useBeacon } from '@/stores/beacon';

interface ProgrammeToml { text: string; sha256: string }

const beacon = useBeacon();
const toml = shallowRef<ProgrammeToml | null>(null);
const text = ref('');
const error = ref<string | null>(null);
const saving = ref(false);
const dirty = computed(() => !!toml.value && text.value !== toml.value.text);

async function load() {
  error.value = null;
  try {
    toml.value = await api<ProgrammeToml>('/api/admin/programme-toml');
    text.value = toml.value.text;
  } catch (e) { error.value = (e as Error).message; }
}

async function save() {
  if (!toml.value) return;
  saving.value = true;
  error.value = null;
  try {
    toml.value = await api<ProgrammeToml>('/api/admin/programme-toml', {
      method: 'PUT', body: { text: text.value, sha256: toml.value.sha256 } });
    text.value = toml.value.text;
    beacon.toast('programme.toml saved. Jobs from now on use the new settings.');
  } catch (e) { error.value = (e as Error).message; }
  saving.value = false;
}

onMounted(load);
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center">
      <div class="text-subtitle1 text-weight-medium">programme.toml</div>
      <q-space />
      <q-btn flat dense no-caps icon="refresh" label="Reload" :disable="saving" @click="load" />
    </q-card-section>
    <q-card-section class="text-caption text-grey-7 q-pt-none">
      Pipeline settings for the whole programme, such as the compose layout, delivery spec and validation rules.
      Anything not set here takes its default. bcn checks the file before it is saved, and jobs that start after a save use the new settings.
    </q-card-section>
    <q-card-section class="q-pt-none">
      <q-banner v-if="error" class="bg-negative text-white q-mb-sm" rounded dense>{{ error }}</q-banner>
      <q-input v-model="text" type="textarea" outlined autogrow spellcheck="false" :disable="!toml"
        input-class="text-mono text-caption" input-style="min-height: 320px; white-space: pre; overflow-x: auto;" />
      <div class="row items-center q-gutter-sm q-mt-sm">
        <q-btn unelevated no-caps color="primary" icon="save" label="Save" :loading="saving" :disable="!dirty" @click="save" />
        <q-btn flat no-caps label="Discard changes" :disable="!dirty || saving" @click="text = toml?.text ?? ''" />
        <span v-if="dirty" class="text-caption text-grey-7">Unsaved changes</span>
      </div>
    </q-card-section>
  </q-card>
</template>

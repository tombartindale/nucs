<script setup lang="ts">
// A module's Claude Project: the producer's checklist for setting up (or updating) the
// Project the module leader is interviewed in, with the instructions to paste and the
// knowledge files to upload. Both are rendered by bcn project from authoring/ in the repo,
// into <module>/build/claude-project/; "stale" means authoring/ has changed since.
import { computed, ref } from 'vue';
import { fileUrl } from '@/api';
import { fmtAgo } from '@/format';
import { useBeacon } from '@/stores/beacon';

const open = defineModel<boolean>({ default: false });
const props = defineProps<{ module: string }>();
const beacon = useBeacon();
const busy = ref(false);

const m = computed(() => beacon.status?.summary.modules[props.module]);
const pack = computed(() => m.value?.claude_project ?? null);
const projectName = computed(() => (pack.value?.title ? `${props.module} – ${pack.value.title}` : props.module));
const files = ref<string[]>([]);
const producerDocs = ref<string[]>([]);

// pack.json lists the knowledge files and the documents only the producer has.
async function loadPack() {
  if (!pack.value?.exists) { files.value = []; producerDocs.value = []; return; }
  try {
    const res = await fetch(fileUrl(`${pack.value.path}/pack.json`, pack.value.version));
    const info = await res.json() as { files?: string[]; producer_docs?: string[] };
    files.value = info.files ?? [];
    producerDocs.value = info.producer_docs ?? [];
  } catch { files.value = []; }
}

async function copyInstructions() {
  if (!pack.value?.exists) return;
  try {
    const res = await fetch(fileUrl(`${pack.value.path}/instructions.md`, pack.value.version));
    if (!res.ok) throw new Error(res.statusText);
    await navigator.clipboard.writeText(await res.text());
    beacon.toast('Instructions copied. Paste them into the Project\'s Instructions box.');
  } catch { beacon.toast('Could not copy the instructions; download them instead.', true); }
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); beacon.toast('Copied'); } catch { beacon.toast('Could not copy', true); }
}

async function regenerate() {
  busy.value = true;
  try {
    const job = await beacon.runJob('project', [props.module], {});
    const result = await beacon.awaitJob(job.id);
    if (result.state === 'done') {
      await beacon.loadStatus();
      await loadPack();
      beacon.toast('Claude Project pack updated.');
    } else beacon.toast('The pack could not be made; see Jobs.', true);
  } finally { busy.value = false; }
}
</script>

<template>
  <q-dialog v-model="open" @show="loadPack">
    <q-card style="width: 640px; max-width: 95vw">
      <q-card-section class="row items-center">
        <q-icon name="smart_toy" size="sm" class="q-mr-sm" />
        <div class="text-h6">Claude Project for {{ module }}</div>
        <q-space />
        <q-btn flat round dense icon="close" v-close-popup aria-label="Close" />
      </q-card-section>

      <q-card-section class="q-pt-none text-body2">
        The module leader writes this module's content by being interviewed in a Claude Project. Set it up once with the
        instructions and templates below; the course map it produces comes back to you at gate 1.
      </q-card-section>

      <q-card-section v-if="!pack?.exists" class="q-pt-none">
        <q-banner dense rounded class="bg-grey-2 text-black">
          No pack has been made for {{ module }} yet.
          <template #action><q-btn unelevated dense no-caps color="primary" label="Make pack" :loading="busy" @click="regenerate" /></template>
        </q-banner>
      </q-card-section>

      <template v-else>
        <q-card-section v-if="pack.stale" class="q-pt-none">
          <q-banner dense rounded class="bg-orange-1 text-black">
            <template #avatar><q-icon name="update" color="orange-8" /></template>
            The instructions or templates have changed since this pack was made. Update the pack, then replace the Project's
            instructions and knowledge files with the new ones.
            <template #action><q-btn unelevated dense no-caps color="primary" label="Update pack" :loading="busy" @click="regenerate" /></template>
          </q-banner>
        </q-card-section>

        <q-list>
          <q-item>
            <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">1</q-avatar></q-item-section>
            <q-item-section>
              <q-item-label>Create a Project in Claude</q-item-label>
              <div class="row items-center no-wrap q-gutter-x-sm q-mt-xs">
                <code class="col bg-grey-2 text-black q-px-sm rounded-borders">{{ projectName }}</code>
                <q-btn flat dense round size="sm" icon="content_copy" aria-label="Copy Project name" @click="copy(projectName)" />
              </div>
            </q-item-section>
          </q-item>

          <q-item>
            <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">2</q-avatar></q-item-section>
            <q-item-section>
              <q-item-label>Paste the instructions</q-item-label>
              <q-item-label caption>Into the Project's Instructions box, replacing anything already there.</q-item-label>
            </q-item-section>
            <q-item-section side>
              <div class="row items-center gap-xs">
                <q-btn unelevated dense no-caps color="primary" icon="content_copy" label="Copy" @click="copyInstructions" />
                <q-btn flat dense round icon="download" aria-label="Download instructions"
                  :href="fileUrl(`${pack.path}/instructions.md`, pack.version, `download=${module}-instructions.md`)" />
              </div>
            </q-item-section>
          </q-item>

          <q-item>
            <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">3</q-avatar></q-item-section>
            <q-item-section>
              <q-item-label>Add the templates to the Project's knowledge</q-item-label>
              <q-item-label caption>Unzip, then add every file: {{ files.join(', ') }}</q-item-label>
            </q-item-section>
            <q-item-section side>
              <q-btn unelevated dense no-caps color="primary" icon="download" label="Templates"
                :href="fileUrl(pack.zip, pack.version, `download=${module}-claude-project-templates.zip`)" />
            </q-item-section>
          </q-item>

          <q-item>
            <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">4</q-avatar></q-item-section>
            <q-item-section>
              <q-item-label>Add your own documents</q-item-label>
              <q-item-label caption>NUCS doesn't have these; add them from your copies.</q-item-label>
              <ul class="q-my-xs q-pl-md text-body2">
                <li v-for="d in producerDocs" :key="d">{{ d }}</li>
              </ul>
            </q-item-section>
          </q-item>

          <q-item>
            <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">5</q-avatar></q-item-section>
            <q-item-section>
              <q-item-label>Share the Project with the module leader</q-item-label>
            </q-item-section>
          </q-item>
        </q-list>

        <q-separator />
        <q-card-section class="row items-center text-caption text-grey-7">
          <span>
            Pack {{ pack.version }} · {{ pack.units }} units<template v-if="pack.generated"> · made {{ fmtAgo(pack.generated, beacon.now) }}</template>
          </span>
          <q-space />
          <q-btn v-if="!pack.stale" flat dense no-caps size="sm" icon="refresh" label="Remake pack" :loading="busy" @click="regenerate" />
        </q-card-section>
      </template>
    </q-card>
  </q-dialog>
</template>

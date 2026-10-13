<script setup lang="ts">
// Admin: the server-wide settings and maintenance only administrators see. Each tab has its
// own URL (/admin, /admin/theme) so a tab can be linked to directly.
//
// The "Backups" tab is hidden for now (not deleted -- see BackupsPanel.vue): it still only
// knows about the pre-redesign full/db S3 prefixes, and deploy/backup/backup.sh no longer
// writes full/ at all. Finding/restoring a backup is a CLI/S3-console job until this tab is
// rebuilt against the new programme/manifest prefixes.
import { ref } from 'vue';
import PageHeader from '@/components/PageHeader.vue';
import ProgrammeTomlEditor from '@/components/admin/ProgrammeTomlEditor.vue';
import CustomTheme from '@/components/admin/CustomTheme.vue';
import ThemeCheck from '@/components/admin/ThemeCheck.vue';

defineProps<{ tab: 'programme' | 'theme' }>();
// Bumped after a theme upload or switch, so the active-theme check re-runs.
const themeVersion = ref(0);
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Admin" sub="Programme settings and the active theme. Only administrators see this page." />
    <q-tabs dense no-caps align="left" class="q-mb-md" active-color="primary" indicator-color="primary">
      <q-route-tab name="programme" to="/admin" exact label="Programme settings" />
      <q-route-tab name="theme" to="/admin/theme" exact label="Theme" />
    </q-tabs>
    <ProgrammeTomlEditor v-if="tab === 'programme'" />
    <template v-else>
      <CustomTheme @changed="themeVersion++" />
      <ThemeCheck :key="themeVersion" class="q-mt-md" />
    </template>
  </q-page>
</template>

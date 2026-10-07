<script setup lang="ts">
// Admin: the server-wide settings and maintenance only administrators see. Each tab has its
// own URL (/admin, /admin/theme, /admin/backups) so a tab can be linked to directly.
import { ref } from 'vue';
import PageHeader from '@/components/PageHeader.vue';
import BackupsPanel from '@/components/admin/BackupsPanel.vue';
import ProgrammeTomlEditor from '@/components/admin/ProgrammeTomlEditor.vue';
import CustomTheme from '@/components/admin/CustomTheme.vue';
import ThemeCheck from '@/components/admin/ThemeCheck.vue';

defineProps<{ tab: 'programme' | 'theme' | 'backups' }>();
// Bumped after a theme upload or switch, so the active-theme check re-runs.
const themeVersion = ref(0);
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Admin" sub="Programme settings, the active theme, and backups. Only administrators see this page." />
    <q-tabs dense no-caps align="left" class="q-mb-md" active-color="primary" indicator-color="primary">
      <q-route-tab name="programme" to="/admin" exact label="Programme settings" />
      <q-route-tab name="theme" to="/admin/theme" exact label="Theme" />
      <q-route-tab name="backups" to="/admin/backups" exact label="Backups" />
    </q-tabs>
    <ProgrammeTomlEditor v-if="tab === 'programme'" />
    <template v-else-if="tab === 'theme'">
      <CustomTheme @changed="themeVersion++" />
      <ThemeCheck :key="themeVersion" class="q-mt-md" />
    </template>
    <BackupsPanel v-else />
  </q-page>
</template>

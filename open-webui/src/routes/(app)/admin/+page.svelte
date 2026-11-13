<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	import { config } from '$lib/stores';

	onMount(() => {
		goto(($config?.features?.enable_admin_analytics ?? true) ? '/admin/analytics' : '/admin/settings');
	});
</script>

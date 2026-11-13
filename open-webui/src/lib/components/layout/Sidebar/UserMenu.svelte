<script lang="ts">
	import { createEventDispatcher, getContext, tick } from 'svelte';

	import { goto } from '$app/navigation';

	import { showSettings, mobile, showSidebar } from '$lib/stores';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import ArchiveBox from '$lib/components/icons/ArchiveBox.svelte';
	import Settings from '$lib/components/icons/Settings.svelte';
	import UserGroup from '$lib/components/icons/UserGroup.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let role = '';

	export let className = 'w-[240px]';
	export let align = 'end';

	const dispatch = createEventDispatcher();
</script>

<!-- svelte-ignore a11y-no-static-element-interactions -->
<Dropdown bind:show onOpenChange={(state) => dispatch('change', state)} {align}>
	<slot />

	<div slot="content">
		<div
			class="{className} rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white/85 dark:bg-gray-850/85 backdrop-blur-xl dark:text-white shadow-lg text-sm"
		>
			<button
				class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
				type="button"
				on:click={async () => {
					show = false;

					await showSettings.set(true);

					if ($mobile) {
						await tick();
						showSidebar.set(false);
					}
				}}
			>
				<div class=" self-center mr-3">
					<Settings className="w-5 h-5" strokeWidth="1.5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Settings')}</div>
			</button>

			<button
				class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
				type="button"
				on:click={async () => {
					show = false;

					dispatch('show', 'archived-chat');

					if ($mobile) {
						await tick();

						showSidebar.set(false);
					}
				}}
			>
				<div class=" self-center mr-3">
					<ArchiveBox className="size-5" strokeWidth="1.5" />
				</div>
				<div class=" self-center truncate">{$i18n.t('Archived Chats')}</div>
			</button>

			{#if role === 'admin'}
				<a
					href="/admin"
					draggable="false"
					class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
					on:click={async (e) => {
						if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) {
							return;
						}
						e.preventDefault();
						show = false;
						goto('/admin');
						if ($mobile) {
							await tick();
							showSidebar.set(false);
						}
					}}
				>
					<div class=" self-center mr-3">
						<UserGroup className="w-5 h-5" strokeWidth="1.5" />
					</div>
					<div class=" self-center truncate">{$i18n.t('Admin Panel')}</div>
				</a>
			{/if}
		</div>
	</div>
</Dropdown>

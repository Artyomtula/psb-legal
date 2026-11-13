<script lang="ts">
	import { getContext, tick } from 'svelte';

	import { tools as _tools, toolServers } from '$lib/stores';

	import { getOAuthClientAuthorizationUrl } from '$lib/apis/configs';
	import { getTools } from '$lib/apis/tools';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';

	const i18n = getContext('i18n');

	type ToolEntry = {
		name: string;
		description: string;
		enabled: boolean;
		authenticated?: boolean;
	};

	export let selectedToolIds: string[] = [];
	export let onClose: Function;

	let show = false;
	let tools: Record<string, ToolEntry> | null = null;

	$: if (show) {
		init();
	}

	const init = async () => {
		if ($_tools === null) {
			await _tools.set(await getTools(localStorage.token).catch(() => []));
		}

		const entries: [string, ToolEntry][] = ($_tools ?? []).map((tool) => [
			tool.id,
			{
				name: tool.name,
				description: tool.meta?.description ?? '',
				enabled: selectedToolIds.includes(tool.id),
				authenticated: tool.authenticated
			}
		]);

		($toolServers ?? []).forEach((server, serverIdx) => {
			if (server.info) {
				const id = `direct_server:${serverIdx}`;
				entries.push([
					id,
					{
						name: server.info.title ?? server.url,
						description: server.info.description ?? '',
						enabled: selectedToolIds.includes(id)
					}
				]);
			}
		});

		const toolMap = Object.fromEntries(entries);
		tools = toolMap;
		selectedToolIds = selectedToolIds.filter((id) => id in toolMap);
	};

	const toggleTool = async (toolId: string) => {
		if (!tools) return;

		if (!(tools[toolId]?.authenticated ?? true)) {
			const serverId = toolId.split(':').at(-1) ?? toolId;
			sessionStorage.setItem('pendingOAuthToolId', toolId);
			window.open(getOAuthClientAuthorizationUrl(serverId, 'mcp'), '_self', 'noopener');
			return;
		}

		tools[toolId].enabled = !tools[toolId].enabled;
		await tick();

		selectedToolIds = tools[toolId].enabled
			? [...selectedToolIds, toolId]
			: selectedToolIds.filter((id) => id !== toolId);
	};
</script>

<Dropdown
	bind:show
	onOpenChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('Tools')} placement="top">
		<slot />
	</Tooltip>
	<div slot="content">
		<div
			class="min-w-70 max-w-70 rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white/85 dark:bg-gray-850/85 backdrop-blur-xl dark:text-white shadow-lg max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin"
		>
			{#if tools === null}
				<div class="py-4">
					<Spinner />
				</div>
			{:else if Object.keys(tools).length === 0}
				<div class="px-3 py-1.5 text-sm text-gray-500">{$i18n.t('No tools found')}</div>
			{:else}
				{#each Object.keys(tools) as toolId (toolId)}
					<button
						class="relative flex w-full justify-between gap-2 items-center px-3 py-1.5 text-sm cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800/50"
						on:click={() => toggleTool(toolId)}
					>
						{#if !(tools[toolId]?.authenticated ?? true)}
							<div class="absolute inset-0 opacity-50 rounded-xl cursor-pointer z-10" />
						{/if}
						<div class="flex-1 truncate">
							<div class="flex flex-1 gap-2 items-center">
								<div class="shrink-0">
									<Wrench />
								</div>
								<Tooltip content={tools[toolId]?.description ?? ''} placement="top-start">
									<div class="truncate">{tools[toolId].name}</div>
								</Tooltip>
							</div>
						</div>

						<div class="shrink-0">
							<Switch state={tools[toolId].enabled} />
						</div>
					</button>
				{/each}
			{/if}
		</div>
	</div>
</Dropdown>

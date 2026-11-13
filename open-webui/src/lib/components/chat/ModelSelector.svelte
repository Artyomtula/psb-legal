<script lang="ts">
	import { getContext } from 'svelte';

	import { models, settings, user } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';

	import Selector from './ModelSelector/Selector.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Plus from '../icons/Plus.svelte';
	import Minus from '../icons/Minus.svelte';

	const i18n = getContext('i18n');

	export let selectedModels = [''];
	export let disabled = false;

	const pinModelHandler = async (modelId: string) => {
		const pinnedModels = $settings?.pinnedModels ?? [];

		settings.set({
			...$settings,
			pinnedModels: pinnedModels.includes(modelId)
				? pinnedModels.filter((id) => id !== modelId)
				: [...new Set([...pinnedModels, modelId])]
		});
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

	$: items = $models.map((model) => ({
		value: model.id,
		label: model.name,
		model: model
	}));

	$: canAddModel = $user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true);

	$: if (selectedModels.length > 0 && $models.length > 0) {
		const modelIds = new Set($models.map((m) => m.id));
		const validModels = selectedModels.map((model) => (modelIds.has(model) ? model : ''));

		if (validModels.some((model, idx) => model !== selectedModels[idx])) {
			selectedModels = validModels;
		}
	}
</script>

<div class="flex flex-col w-full items-start">
	{#each selectedModels as _, selectedModelIdx}
		<div class="flex w-full max-w-fit items-center">
			<div class="overflow-hidden w-full">
				<div class="max-w-full mr-1">
					<Selector
						id={`${selectedModelIdx}`}
						placeholder={$i18n.t('Select a model')}
						searchEnabled={false}
						showFilters={false}
						{items}
						{pinModelHandler}
						bind:value={selectedModels[selectedModelIdx]}
					/>
				</div>
			</div>

			{#if canAddModel}
				{#if selectedModelIdx === 0}
					<Tooltip content={$i18n.t('Add Model')}>
						<button
							class="self-center mx-1 p-1 rounded-lg text-gray-500 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-850 transition disabled:opacity-50"
							type="button"
							{disabled}
							aria-label={$i18n.t('Add Model')}
							on:click={() => {
								selectedModels = [...selectedModels, ''];
							}}
						>
							<Plus className="size-3.5" strokeWidth="2" />
						</button>
					</Tooltip>
				{:else}
					<Tooltip content={$i18n.t('Remove Model')}>
						<button
							class="self-center mx-1 p-1 rounded-lg text-gray-500 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-850 transition disabled:opacity-50"
							type="button"
							{disabled}
							aria-label={$i18n.t('Remove Model')}
							on:click={() => {
								selectedModels = selectedModels.filter((__, i) => i !== selectedModelIdx);
							}}
						>
							<Minus className="size-3.5" strokeWidth="2" />
						</button>
					</Tooltip>
				{/if}
			{/if}
		</div>
	{/each}
</div>

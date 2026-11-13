<script lang="ts">
	import { marked } from 'marked';

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { config, models as _models, settings } from '$lib/stores';
	import {
		createNewModel,
		getBaseModels,
		toggleModelById,
		updateModelById
	} from '$lib/apis/models';
	import { page } from '$app/stores';

	import { getModels } from '$lib/apis';
	import Search from '$lib/components/icons/Search.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import ModelEditor from '$lib/components/admin/Settings/Models/ModelEditor.svelte';
	import { toast } from 'svelte-sonner';
	import ModelSettingsModal from './Models/ModelSettingsModal.svelte';
	import ManageModelsModal from './Models/ManageModelsModal.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Pagination from '$lib/components/common/Pagination.svelte';

	let models = null;

	let workspaceModels = null;
	let baseModels = null;

	let filteredModels = [];
	let selectedModelId = null;

	let showConfigModal = false;
	let showManageModal = false;

	const perPage = 30;
	let currentPage = 1;

	$: if (models) {
		filteredModels = models
			.filter((m) => searchValue === '' || m.name.toLowerCase().includes(searchValue.toLowerCase()))
			.sort((a, b) => {
				return (a?.name ?? a?.id ?? '').localeCompare(b?.name ?? b?.id ?? '');
			});
	}

	let searchValue = '';

	$: if (searchValue !== undefined) {
		currentPage = 1;
	}

	const init = async () => {
		models = null;

		workspaceModels = await getBaseModels(localStorage.token);
		baseModels = await getModels(localStorage.token, null, true);

		models = baseModels.map((m) => {
			const workspaceModel = workspaceModels.find((wm) => wm.id === m.id);

			if (workspaceModel) {
				return {
					...m,
					...workspaceModel
				};
			} else {
				return {
					...m,
					id: m.id,
					name: m.name,

					is_active: true
				};
			}
		});

		_models.set(
			await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
			)
		);
	};

	const upsertModelHandler = async (model, overrides = {}, showToast = true) => {
		model = { ...model, base_model_id: null, ...overrides };

		if (workspaceModels.find((m) => m.id === model.id)) {
			const res = await updateModelById(localStorage.token, model.id, model).catch((error) => {
				return null;
			});

			if (res && showToast) {
				toast.success($i18n.t('Model updated successfully'));
			}
		} else {
			const res = await createNewModel(localStorage.token, {
				meta: {},
				id: model.id,
				name: model.name,
				base_model_id: null,
				params: {},
				access_grants: [],
				...model
			}).catch((error) => {
				return null;
			});

			if (res && showToast) {
				toast.success($i18n.t('Model updated successfully'));
				await init();
			}
		}
	};

	const toggleModelHandler = async (model) => {
		if (!Object.keys(model).includes('base_model_id')) {
			await createNewModel(localStorage.token, {
				id: model.id,
				name: model.name,
				base_model_id: null,
				meta: {},
				params: {},
				access_grants: [],
				is_active: model.is_active
			}).catch((error) => {
				return null;
			});
		} else {
			await toggleModelById(localStorage.token, model.id);
		}

		// await init();
		_models.set(
			await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
			)
		);
	};

	onMount(async () => {
		await init();
		selectedModelId = $page.url.searchParams.get('id');
	});
</script>

<ModelSettingsModal bind:show={showConfigModal} initHandler={init} />
<ManageModelsModal bind:show={showManageModal} />

{#if models !== null}
	{#if selectedModelId === null}
		<div class="flex flex-col gap-1 mt-1.5 mb-2">
			<div class="flex justify-between items-center">
				<div class="flex items-center md:self-center text-xl font-medium px-0.5 gap-2 shrink-0">
					<div>
						{$i18n.t('Models')}
					</div>

					<div class="text-lg font-medium text-gray-500 dark:text-gray-500">
						{filteredModels.length}
					</div>
				</div>

				<div class="flex w-full justify-end gap-1.5">
					<button
						class="flex text-xs items-center space-x-1 px-3 py-1.5 rounded-xl bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-200 transition"
						type="button"
						on:click={() => {
							showManageModal = true;
						}}
					>
						<div class=" self-center font-medium line-clamp-1">
							{$i18n.t('Manage')}
						</div>
					</button>

					<button
						class="flex text-xs items-center space-x-1 px-3 py-1.5 rounded-xl bg-black hover:bg-gray-900 text-white dark:bg-white dark:hover:bg-gray-100 dark:text-black transition font-medium"
						type="button"
						on:click={() => {
							showConfigModal = true;
						}}
					>
						<div class=" self-center font-medium line-clamp-1">
							{$i18n.t('Settings')}
						</div>
					</button>
				</div>
			</div>
		</div>

		<div
			class="py-2 bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl rounded-3xl border border-gray-100/30 dark:border-gray-850/30"
		>
			<div class="px-3.5 flex flex-1 items-center w-full space-x-2 py-0.5 pb-2">
				<div class="flex flex-1 items-center">
					<div class=" self-center ml-1 mr-3">
						<Search className="size-3.5" />
					</div>
					<input
						class=" w-full text-sm py-1 rounded-r-xl outline-hidden bg-transparent"
						bind:value={searchValue}
						placeholder={$i18n.t('Search Models')}
					/>
					{#if searchValue}
						<div class="self-center pl-1.5 translate-y-[0.5px] rounded-l-xl bg-transparent">
							<button
								class="p-0.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"
								on:click={() => {
									searchValue = '';
								}}
							>
								<XMark className="size-3" strokeWidth="2" />
							</button>
						</div>
					{/if}
				</div>
			</div>

			<div class="px-3 my-2" id="model-list">
				{#if filteredModels.length > 0}
					{#each filteredModels.slice((currentPage - 1) * perPage, currentPage * perPage) as model, modelIdx (`${model.id}-${modelIdx}`)}
						<div
							class=" flex space-x-4 cursor-pointer w-full px-3 py-2 dark:hover:bg-white/5 hover:bg-black/5 rounded-xl transition {model
								?.meta?.hidden
								? 'opacity-50 dark:opacity-50'
								: ''}"
							id="model-item-{model.id}"
						>
							<button
								class=" flex flex-1 text-left space-x-3.5 cursor-pointer w-full"
								type="button"
								on:click={() => {
									selectedModelId = model.id;
								}}
							>
								<div class=" self-center w-9">
									<div
										class=" rounded-full object-cover {(model?.is_active ?? true)
											? ''
											: 'opacity-50 dark:opacity-50'} "
									>
										<img
											src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${model.id}`}
											alt="modelfile profile"
											class=" rounded-full w-full h-auto object-cover"
											on:error={(e) => {
												e.target.src = '/favicon.png';
											}}
										/>
									</div>
								</div>

								<div
									class=" flex-1 self-center {(model?.is_active ?? true) ? '' : 'text-gray-500'}"
								>
									<Tooltip
										content={marked.parse(
											!!model?.meta?.description
												? model?.meta?.description
												: model?.ollama?.digest
													? `${model?.ollama?.digest} **(${model?.ollama?.modified_at})**`
													: model.id
										)}
										className=" w-fit"
										placement="top-start"
									>
										<div class="font-medium line-clamp-1 flex items-center gap-2">
											{model.name}
										</div>
									</Tooltip>
									<div
										class=" text-xs overflow-hidden text-ellipsis line-clamp-1 flex items-center gap-1 text-gray-500"
									>
										<span class=" line-clamp-1">
											{!!model?.meta?.description
												? model?.meta?.description
												: model?.ollama?.digest
													? `${model.id} (${model?.ollama?.digest})`
													: model.id}
										</span>
									</div>
								</div>
							</button>
							<div class="flex flex-row gap-0.5 items-center self-center">
								<button
									class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
									type="button"
									on:click={() => {
										selectedModelId = model.id;
									}}
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
										stroke-width="1.5"
										stroke="currentColor"
										class="w-4 h-4"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125"
										/>
									</svg>
								</button>

								<div class="ml-1">
									<Tooltip
										content={(model?.is_active ?? true) ? $i18n.t('Enabled') : $i18n.t('Disabled')}
									>
										<Switch
											bind:state={model.is_active}
											on:change={async () => {
												toggleModelHandler(model);
											}}
										/>
									</Tooltip>
								</div>
							</div>
						</div>
					{/each}
				{:else}
					<div class=" w-full h-full flex flex-col justify-center items-center my-16 mb-24">
						<div class="max-w-md text-center">
							<div class=" text-3xl mb-3">😕</div>
							<div class=" text-lg font-medium mb-1">{$i18n.t('No models found')}</div>
							<div class=" text-gray-500 text-center text-xs">
								{$i18n.t('Try adjusting your search or filter to find what you are looking for.')}
							</div>
						</div>
					</div>
				{/if}
			</div>

			{#if filteredModels.length > perPage}
				<Pagination bind:page={currentPage} count={filteredModels.length} {perPage} />
			{/if}
		</div>
	{:else}
		<ModelEditor
			edit
			model={models.find((m) => m.id === selectedModelId)}
			preset={false}
			onSubmit={(model) => {
				console.log(model);
				upsertModelHandler(model);
				selectedModelId = null;
			}}
			onBack={async () => {
				selectedModelId = null;
				await init();
			}}
		/>
	{/if}
{:else}
	<div class=" h-full w-full flex justify-center items-center">
		<Spinner className="size-5" />
	</div>
{/if}

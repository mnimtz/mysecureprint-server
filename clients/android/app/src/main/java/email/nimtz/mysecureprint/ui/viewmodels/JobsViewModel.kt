package email.nimtz.mysecureprint.ui.viewmodels

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import email.nimtz.mysecureprint.data.api.ApiClient
import email.nimtz.mysecureprint.data.api.ApiResult
import email.nimtz.mysecureprint.data.model.JobsResponse
import email.nimtz.mysecureprint.data.model.PrintJob
import email.nimtz.mysecureprint.data.store.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed class JobsUiState {
    object Loading : JobsUiState()
    data class Success(val jobs: List<PrintJob>) : JobsUiState()
    data class Error(val message: String) : JobsUiState()
}

class JobsViewModel(private val settings: SettingsStore) : ViewModel() {

    private val _uiState = MutableStateFlow<JobsUiState>(JobsUiState.Loading)
    val uiState: StateFlow<JobsUiState> = _uiState

    init { loadJobs() }

    fun loadJobs() {
        viewModelScope.launch {
            _uiState.value = JobsUiState.Loading
            val client = ApiClient(settings.serverUrl, settings.authToken)
            val raw = client.getJobs()
            when (val result = client.parseBody<JobsResponse>(raw)) {
                is ApiResult.Success      -> _uiState.value = JobsUiState.Success(result.body.jobs)
                is ApiResult.Error        -> {
                    if (result.code == 401) settings.logout()
                    _uiState.value = JobsUiState.Error("HTTP ${result.code}: ${result.message}")
                }
                is ApiResult.NetworkError -> _uiState.value = JobsUiState.Error(result.message)
            }
        }
    }

    fun deleteJob(jobId: String) {
        viewModelScope.launch {
            val client = ApiClient(settings.serverUrl, settings.authToken)
            client.deleteJob(jobId)
            loadJobs()
        }
    }

    fun clearAll() {
        viewModelScope.launch {
            val client = ApiClient(settings.serverUrl, settings.authToken)
            client.clearAllJobs()
            loadJobs()
        }
    }

    fun previewUrl(jobId: String): String =
        ApiClient(settings.serverUrl, settings.authToken).getJobPreviewUrl(jobId)
}

class JobsViewModelFactory(private val settings: SettingsStore) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = JobsViewModel(settings) as T
}

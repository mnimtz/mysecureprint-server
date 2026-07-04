package email.nimtz.mysecureprint.ui.viewmodels

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import email.nimtz.mysecureprint.data.api.ApiClient
import email.nimtz.mysecureprint.data.api.ApiResult
import email.nimtz.mysecureprint.data.model.EntraDeviceCodeResponse
import email.nimtz.mysecureprint.data.store.SettingsStore
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed class LoginUiState {
    object Idle : LoginUiState()
    data class Loading(val source: String) : LoginUiState()
    data class EntraDeviceCode(
        val userCode: String,
        val verificationUri: String,
        val deviceCode: String,
        val interval: Int,
    ) : LoginUiState()
    object Success : LoginUiState()
    data class Error(val message: String) : LoginUiState()
}

class LoginViewModel(private val settings: SettingsStore) : ViewModel() {

    private val _uiState = MutableStateFlow<LoginUiState>(LoginUiState.Idle)
    val uiState: StateFlow<LoginUiState> = _uiState

    private var pollJob: Job? = null

    fun clearError() { if (_uiState.value is LoginUiState.Error) _uiState.value = LoginUiState.Idle }

    fun loginWithPassword(username: String, password: String) {
        viewModelScope.launch {
            _uiState.value = LoginUiState.Loading("password")
            val client = ApiClient(settings.serverUrl)
            val raw = client.login(username, password)
            val result = client.parseBody<email.nimtz.mysecureprint.data.model.LoginResponse>(raw)
            when (result) {
                is ApiResult.Success -> {
                    val resp = result.body
                    settings.saveLogin(
                        token    = resp.token,
                        username = resp.user.username,
                        email    = resp.user.email ?: username,
                        isAdmin  = resp.user.isAdmin,
                    )
                    _uiState.value = LoginUiState.Success
                }
                is ApiResult.Error       -> _uiState.value = LoginUiState.Error("HTTP ${result.code}: ${result.message}")
                is ApiResult.NetworkError -> _uiState.value = LoginUiState.Error(result.message)
            }
        }
    }

    fun startEntraDeviceCode(context: Context) {
        viewModelScope.launch {
            _uiState.value = LoginUiState.Loading("entra")
            val client = ApiClient(settings.serverUrl)
            val raw = client.startEntraLogin()
            val result = client.parseBody<EntraDeviceCodeResponse>(raw)
            when (result) {
                is ApiResult.Success -> {
                    val resp = result.body
                    _uiState.value = LoginUiState.EntraDeviceCode(
                        userCode        = resp.userCode,
                        verificationUri = resp.verificationUri,
                        deviceCode      = resp.deviceCode,
                        interval        = resp.interval.coerceAtLeast(5),
                    )
                    // Open browser automatically
                    try {
                        context.startActivity(
                            Intent(Intent.ACTION_VIEW, Uri.parse(resp.verificationUri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        )
                    } catch (_: Exception) {}
                    startPolling(client, resp.deviceCode, resp.interval.coerceAtLeast(5))
                }
                is ApiResult.Error       -> _uiState.value = LoginUiState.Error("HTTP ${result.code}: ${result.message}")
                is ApiResult.NetworkError -> _uiState.value = LoginUiState.Error(result.message)
            }
        }
    }

    private fun startPolling(client: ApiClient, deviceCode: String, intervalSec: Int) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            repeat(60) {
                delay(intervalSec * 1000L)
                val raw = client.pollEntraLogin(deviceCode)
                val result = client.parseBody<email.nimtz.mysecureprint.data.model.EntraPollResponse>(raw)
                if (result is ApiResult.Success) {
                    val resp = result.body
                    when (resp.status) {
                        "ok" -> {
                            val token = resp.token ?: ""
                            if (token.isNotBlank()) {
                                settings.saveLogin(
                                    token    = token,
                                    username = resp.user?.username ?: "",
                                    email    = resp.user?.email ?: "",
                                    isAdmin  = resp.user?.isAdmin ?: false,
                                )
                                _uiState.value = LoginUiState.Success
                                return@launch
                            }
                        }
                        "error" -> {
                            _uiState.value = LoginUiState.Error(resp.error ?: "Microsoft-Login fehlgeschlagen.")
                            return@launch
                        }
                        // "pending" → continue
                    }
                }
            }
            _uiState.value = LoginUiState.Error("Microsoft-Login abgelaufen. Bitte erneut versuchen.")
        }
    }

    override fun onCleared() { pollJob?.cancel() }
}

class LoginViewModelFactory(private val settings: SettingsStore) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T =
        LoginViewModel(settings) as T
}

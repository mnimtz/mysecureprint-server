package email.nimtz.mysecureprint.ui.screens

import android.app.Activity
import android.nfc.NfcAdapter
import android.nfc.Tag
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import email.nimtz.mysecureprint.data.model.CardInfo
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors
import email.nimtz.mysecureprint.ui.viewmodels.CardsUiState
import email.nimtz.mysecureprint.ui.viewmodels.CardsViewModel
import email.nimtz.mysecureprint.ui.viewmodels.CardsViewModelFactory

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CardsScreen(settings: SettingsStore) {
    val context = LocalContext.current
    val vm: CardsViewModel = viewModel(factory = CardsViewModelFactory(settings))
    val uiState by vm.uiState.collectAsState()
    val actionResult by vm.actionResult.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }
    var showNfcDialog by remember { mutableStateOf(false) }
    var showManualEntry by remember { mutableStateOf(false) }
    var manualUid by remember { mutableStateOf("") }

    LaunchedEffect(actionResult) {
        if (actionResult.isNotBlank()) {
            snackbarHostState.showSnackbar(actionResult)
            vm.clearActionResult()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Karten") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MSPColors.Navy, titleContentColor = Color.White,
                    actionIconContentColor = Color.White),
                actions = {
                    IconButton(onClick = {
                        val nfcAdapter = NfcAdapter.getDefaultAdapter(context)
                        if (nfcAdapter != null && nfcAdapter.isEnabled) {
                            showNfcDialog = true
                        } else {
                            showManualEntry = true
                        }
                    }) {
                        Icon(Icons.Default.AddCard, contentDescription = "Karte hinzufügen")
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = {
                    val nfcAdapter = NfcAdapter.getDefaultAdapter(context)
                    if (nfcAdapter != null && nfcAdapter.isEnabled) showNfcDialog = true
                    else showManualEntry = true
                },
                containerColor = MSPColors.Cyan,
                contentColor = Color.Black,
            ) {
                Icon(Icons.Default.Nfc, null)
                Spacer(Modifier.width(8.dp))
                Text("Karte scannen")
            }
        }
    ) { padding ->
        val isRefreshing = uiState is CardsUiState.Loading
        PullToRefreshBox(isRefreshing = isRefreshing, onRefresh = { vm.load() },
            modifier = Modifier.padding(padding)) {
            when (val state = uiState) {
                is CardsUiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    CircularProgressIndicator(color = MSPColors.Cyan)
                }
                is CardsUiState.Error   -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Default.ErrorOutline, null, tint = MSPColors.Error, modifier = Modifier.size(48.dp))
                        Spacer(Modifier.height(8.dp))
                        Text(state.message)
                        Spacer(Modifier.height(16.dp))
                        Button(onClick = { vm.load() }) { Text("Nochmal") }
                    }
                }
                is CardsUiState.Success -> {
                    if (state.cards.isEmpty()) {
                        Box(Modifier.fillMaxSize(), Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Icon(Icons.Default.CreditCardOff, null,
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.size(56.dp))
                                Spacer(Modifier.height(8.dp))
                                Text("Keine Karten", color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.height(4.dp))
                                Text("Tippe + um eine Karte zu registrieren",
                                    color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                            }
                        }
                    } else {
                        LazyColumn(modifier = Modifier.fillMaxSize()) {
                            items(state.cards, key = { it.id }) { card ->
                                CardRow(card = card, onDelete = { vm.deleteCard(card.id) })
                                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f))
                            }
                        }
                    }
                }
            }
        }
    }

    // NFC scan dialog
    if (showNfcDialog) {
        NfcScanDialog(
            onDismiss = { showNfcDialog = false },
            onTagScanned = { uid ->
                showNfcDialog = false
                vm.registerCard(uid)
            },
        )
    }

    // Manual UID entry
    if (showManualEntry) {
        AlertDialog(
            onDismissRequest = { showManualEntry = false; manualUid = "" },
            title = { Text("Karten-UID eingeben") },
            text = {
                OutlinedTextField(
                    value = manualUid,
                    onValueChange = { manualUid = it },
                    label = { Text("UID (hex)") },
                    singleLine = true,
                    placeholder = { Text("z.B. 04:AB:CD:EF") },
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    if (manualUid.isNotBlank()) {
                        vm.registerCard(manualUid.uppercase().replace(" ", "").replace(":", ""))
                        showManualEntry = false; manualUid = ""
                    }
                }) { Text("Registrieren") }
            },
            dismissButton = { TextButton(onClick = { showManualEntry = false; manualUid = "" }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun CardRow(card: CardInfo, onDelete: () -> Unit) {
    var showDeleteConfirm by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Default.CreditCard, null, tint = MSPColors.Cyan, modifier = Modifier.size(24.dp))
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(card.uid, fontWeight = FontWeight.Medium, fontSize = 14.sp,
                color = MaterialTheme.colorScheme.onSurface)
            if (card.profileName.isNotBlank()) {
                Text(card.profileName, fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        IconButton(onClick = { showDeleteConfirm = true }) {
            Icon(Icons.Default.Delete, null, tint = MSPColors.Error)
        }
    }
    if (showDeleteConfirm) {
        AlertDialog(
            onDismissRequest = { showDeleteConfirm = false },
            title = { Text("Karte löschen?") },
            text = { Text(card.uid) },
            confirmButton = {
                TextButton(onClick = { onDelete(); showDeleteConfirm = false },
                    colors = ButtonDefaults.textButtonColors(contentColor = MSPColors.Error)) {
                    Text("Löschen")
                }
            },
            dismissButton = { TextButton(onClick = { showDeleteConfirm = false }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun NfcScanDialog(onDismiss: () -> Unit, onTagScanned: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("NFC-Karte scannen") },
        text = {
            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Default.Nfc, null, tint = MSPColors.Cyan, modifier = Modifier.size(64.dp))
                Spacer(Modifier.height(16.dp))
                Text("Halte die Karte an die Rückseite des Geräts.",
                    color = MaterialTheme.colorScheme.onSurface)
                Spacer(Modifier.height(8.dp))
                CircularProgressIndicator(color = MSPColors.Cyan, modifier = Modifier.size(32.dp))
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text("Abbrechen") } },
    )
}

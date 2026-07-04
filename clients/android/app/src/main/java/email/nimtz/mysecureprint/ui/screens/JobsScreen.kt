package email.nimtz.mysecureprint.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import email.nimtz.mysecureprint.data.model.PrintJob
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors
import email.nimtz.mysecureprint.ui.viewmodels.JobsUiState
import email.nimtz.mysecureprint.ui.viewmodels.JobsViewModel
import email.nimtz.mysecureprint.ui.viewmodels.JobsViewModelFactory

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JobsScreen(settings: SettingsStore) {
    val vm: JobsViewModel = viewModel(factory = JobsViewModelFactory(settings))
    val uiState by vm.uiState.collectAsState()
    var selectedJob by remember { mutableStateOf<PrintJob?>(null) }
    var showClearConfirm by remember { mutableStateOf(false) }
    val isRefreshing = uiState is JobsUiState.Loading

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Jobs") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MSPColors.Navy,
                    titleContentColor = Color.White,
                    actionIconContentColor = Color.White,
                ),
                actions = {
                    IconButton(onClick = { showClearConfirm = true }) {
                        Icon(Icons.Default.DeleteSweep, contentDescription = "Alle löschen")
                    }
                },
            )
        }
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = isRefreshing,
            onRefresh = { vm.loadJobs() },
            modifier = Modifier.padding(padding),
        ) {
            when (val state = uiState) {
                is JobsUiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    CircularProgressIndicator(color = MSPColors.Cyan)
                }
                is JobsUiState.Error   -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Default.ErrorOutline, null, tint = MSPColors.Error, modifier = Modifier.size(48.dp))
                        Spacer(Modifier.height(8.dp))
                        Text(state.message, color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(16.dp))
                        Button(onClick = { vm.loadJobs() }) { Text("Nochmal") }
                    }
                }
                is JobsUiState.Success -> {
                    if (state.jobs.isEmpty()) {
                        Box(Modifier.fillMaxSize(), Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Icon(Icons.Default.Inbox, null, tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.size(56.dp))
                                Spacer(Modifier.height(8.dp))
                                Text("Keine Jobs", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    } else {
                        LazyColumn(modifier = Modifier.fillMaxSize()) {
                            items(state.jobs, key = { it.jobId }) { job ->
                                JobRow(job = job, onClick = { selectedJob = job })
                                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f))
                            }
                        }
                    }
                }
            }
        }
    }

    // Job detail sheet
    selectedJob?.let { job ->
        JobDetailSheet(
            job = job,
            previewUrl = if (job.hasPreview) vm.previewUrl(job.jobId) else null,
            onDismiss = { selectedJob = null },
            onDelete = { vm.deleteJob(job.jobId); selectedJob = null },
        )
    }

    // Clear confirm
    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text("Alle Jobs löschen?") },
            text = { Text("Diese Aktion kann nicht rückgängig gemacht werden.") },
            confirmButton = {
                TextButton(onClick = { vm.clearAll(); showClearConfirm = false },
                    colors = ButtonDefaults.textButtonColors(contentColor = MSPColors.Error)) {
                    Text("Löschen")
                }
            },
            dismissButton = { TextButton(onClick = { showClearConfirm = false }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun JobRow(job: PrintJob, onClick: () -> Unit) {
    val (statusColor, statusLabel) = when (job.status.lowercase()) {
        "pending", "queued"            -> MSPColors.Warning to "Ausstehend"
        "processing", "rendering"      -> MSPColors.Cyan   to "Verarbeitung"
        "printed", "ok", "success",
        "completed"                    -> MSPColors.Success to "Gedruckt"
        "error", "failed"              -> MSPColors.Error   to "Fehler"
        "expired"                      -> Color(0xFF888888) to "Abgelaufen"
        else                           -> MaterialTheme.colorScheme.onSurfaceVariant to job.status
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Status indicator
        Box(
            modifier = Modifier
                .size(10.dp)
                .background(statusColor, shape = RoundedCornerShape(5.dp))
        )
        Spacer(Modifier.width(12.dp))

        Column(modifier = Modifier.weight(1f)) {
            Text(
                job.filename,
                fontWeight = FontWeight.Medium,
                fontSize = 14.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Spacer(Modifier.height(2.dp))
            Row {
                Text(statusLabel, fontSize = 12.sp, color = statusColor)
                if (job.queue.isNotBlank()) {
                    Text("  ·  ${job.queue}", fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                }
            }
            if (!job.aiSummary.isNullOrBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(job.aiSummary, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }

        Spacer(Modifier.width(8.dp))
        Icon(Icons.Default.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun JobDetailSheet(
    job: PrintJob,
    previewUrl: String?,
    onDismiss: () -> Unit,
    onDelete: () -> Unit,
) {
    var showDeleteConfirm by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp)
            .padding(bottom = 32.dp)) {

            Text(job.filename, fontWeight = FontWeight.Bold, fontSize = 17.sp,
                color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(4.dp))
            Text(job.queue, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))

            // Status
            DetailRow("Status", job.status)
            DetailRow("Erstellt", job.createdAt)
            job.forwardedAt?.let { DetailRow("Gedruckt", it) }
            job.sourceIdentity?.let { DetailRow("Quelle", it) }
            job.delegatedFrom?.let { DetailRow("Von", it) }
            job.hostname?.let { DetailRow("Host", it) }
            job.errorMessage?.let { DetailRow("Fehler", it) }

            // AI analysis
            if (!job.aiSummary.isNullOrBlank() || !job.aiDocType.isNullOrBlank()) {
                Spacer(Modifier.height(12.dp))
                Text("KI-Analyse", fontWeight = FontWeight.SemiBold, fontSize = 14.sp,
                    color = MSPColors.Cyan)
                Spacer(Modifier.height(4.dp))
                job.aiDocType?.let { DetailRow("Dokumenttyp", it) }
                job.aiColorRec?.let { DetailRow("Farbmodus", it) }
                job.aiSensitivity?.let { DetailRow("Vertraulichkeit", it) }
                job.aiSummary?.let { DetailRow("Zusammenfassung", it) }
                job.aiTags?.let { if (it.isNotBlank()) DetailRow("Schlagwörter", it) }

                // Custom extra fields
                val extraJson = job.aiExtra
                if (!extraJson.isNullOrBlank() && extraJson != "{}") {
                    try {
                        val type = object : TypeToken<Map<String, String>>() {}.type
                        val map: Map<String, String> = Gson().fromJson(extraJson, type)
                        map.entries.sortedBy { it.key }.forEach { (k, v) ->
                            if (v.isNotBlank()) DetailRow(k, v)
                        }
                    } catch (_: Exception) {}
                }
            }

            Spacer(Modifier.height(20.dp))

            // Delete button
            OutlinedButton(
                onClick = { showDeleteConfirm = true },
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = MSPColors.Error),
                border = ButtonDefaults.outlinedButtonBorder.copy(width = 1.dp),
            ) {
                Icon(Icons.Default.Delete, null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Job löschen")
            }
        }
    }

    if (showDeleteConfirm) {
        AlertDialog(
            onDismissRequest = { showDeleteConfirm = false },
            title = { Text("Job löschen?") },
            confirmButton = {
                TextButton(onClick = onDelete,
                    colors = ButtonDefaults.textButtonColors(contentColor = MSPColors.Error)) {
                    Text("Löschen")
                }
            },
            dismissButton = { TextButton(onClick = { showDeleteConfirm = false }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(modifier = Modifier
        .fillMaxWidth()
        .padding(vertical = 4.dp)) {
        Text(label, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(120.dp))
        Text(value, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f))
    }
}

provider "google" {
    credentials = "${file("gcloud-account.json")}"
    project = "appsembler-testing"
    region = "us-east1-b"
}

resource "google_compute_firewall" "default" {
  name = "dogwood-terraform-firewall"
  network = "default"

  allow {
    protocol = "tcp"
    ports = ["80"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags = ["web"]
}

resource "google_compute_instance" "default" {
    name = "dogwood-terraform-test"
    machine_type = "n1-standard-1"
    zone = "us-east1-b"
    tags = ["web"]

    network_interface {
      network = "default"
      access_config {
        // Ephemeral IP
      }
    }

    disk {
      image = "dogwood-packer-local"
      type = "pd-standard"
      size = 50
    }

}
